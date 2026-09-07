import os
import json
import logging
import asyncio
import random
from aiohttp import web
from telegram import Update, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from google import genai
from google.genai import types

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_URL = os.environ.get("RENDER_URL")

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Global Data Banks
PROCESSED_DATA = {
    "direct": [],
    "statement": [],
    "twisted": []
}
ASKED_IDS = set()
POLL_TRACKER = {}

# Batch generator to process uploaded JSON in bulk
async def process_all_questions_in_bulk(raw_questions: list):
    prompt = f"""
You are an expert exam setter. Convert the provided array of quiz items into two new formats:
1. "statement": Assertion-Reason style ("कथन (A): ... \nकारण (R): ...").
2. "twisted": Rephrased analytical question with new sentence structure.

Input Data:
{json.dumps(raw_questions, ensure_ascii=False)}

Rules:
- Keep the correct option matching the original index.
- Output strictly valid JSON with this exact array structure:
[
  {{
    "id": 0,
    "direct": {{"question": "...", "options": [...], "answer": 0}},
    "statement": {{"question": "...", "options": [...], "answer": 0}},
    "twisted": {{"question": "...", "options": [...], "answer": 0}}
  }}
]
"""
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        parsed = json.loads(response.text.strip())
        return parsed
    except Exception as e:
        logger.error(f"Bulk Generation Error: {e}")
        # Fallback to direct raw format if API fails during processing
        fallback = []
        for i, q in enumerate(raw_questions):
            fallback.append({
                "id": i,
                "direct": q,
                "statement": q,
                "twisted": q
            })
        return fallback

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧠 **Ultra-Fast Quiz Bot**\n\n"
        "1. `.json` या `.txt` फ़ाइल भेजें (अपलोड के समय ही AI सारे वेरिएशन्स तैयार कर लेगा)।\n"
        "2. फिर बिना किसी देरी के तुरंत क्विज़ खेलें:\n\n"
        "📌 `/quiz 10` - डायरेक्ट सवाल\n"
        "📝 `/statement 10` - कथन-कारण सवाल\n"
        "🔄 `/twisted 10` - घुमावदार सवाल\n"
        "🧹 `/reset` - हिस्ट्री साफ़ करें"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_questions_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PROCESSED_DATA, ASKED_IDS
    doc = update.message.document
    file_name = doc.file_name.lower()
    
    if not (file_name.endswith('.json') or file_name.endswith('.txt')):
        return await update.message.reply_text("❌ केवल .json या .txt फ़ाइल भेजें।")

    status_msg = await update.message.reply_text("📥 फ़ाइल मिल गई। AI सभी फॉर्मेट तैयार कर रहा है, कृपया प्रतीक्षा करें... ⚡")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text_data = content.decode('utf-8').strip()
        data = json.loads(text_data)

        if isinstance(data, list) and len(data) > 0:
            bulk_processed = await process_all_questions_in_bulk(data)
            
            PROCESSED_DATA["direct"] = [item["direct"] for item in bulk_processed]
            PROCESSED_DATA["statement"] = [item["statement"] for item in bulk_processed]
            PROCESSED_DATA["twisted"] = [item["twisted"] for item in bulk_processed]
            
            ASKED_IDS.clear()
            
            await status_msg.edit_text(
                f"✅ **{len(data)} सवाल लोड हो गए!**\n\n"
                "अब कमांड देते ही बिना किसी टाइम-लैग के क्विज़ चालू होगा:\n"
                "👉 `/statement 5` या `/twisted 5` टाइप करें।", 
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text("❌ फ़ाइल में वैलिड प्रश्न लिस्ट नहीं है।")
    except Exception as e:
        logger.error(f"File handling error: {e}")
        await status_msg.edit_text("❌ फ़ाइल रीड या प्रोसेस करने में एरर आई।")

async def start_quiz_session(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global PROCESSED_DATA, ASKED_IDS
    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        return await update.message.reply_text("❌ पहले अपनी प्रश्नों वाली फ़ाइल अपलोड करें!")

    unasked_indices = [i for i in range(len(bank)) if i not in ASKED_IDS]

    if not unasked_indices:
        return await update.message.reply_text(
            "🎉 **सभी सवाल समाप्त हो चुके हैं!**\n"
            "पुनः शुरू करने के लिए `/reset` करें।"
        )

    count = 5
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])

    selected_indices = random.sample(unasked_indices, min(count, len(unasked_indices)))
    
    session_questions = []
    for idx in selected_indices:
        ASKED_IDS.add(idx)
        session_questions.append(bank[idx])

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    context.user_data.clear()
    context.user_data.update({
        "quiz": session_questions,
        "idx": 0,
        "score": 0,
        "total": len(session_questions),
        "busy": True
    })

    await send_next_quiz(context, chat_id, user_id)

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="direct")

async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="statement")

async def twisted_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="twisted")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ASKED_IDS
    ASKED_IDS.clear()
    await update.message.reply_text("🧹 **हिस्ट्री रीसेट कर दी गई है!**")

async def send_next_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    user_data = context.application.user_data.get(user_id)
    if not user_data or not user_data.get("busy"):
        return

    idx = user_data.get("idx", 0)
    quiz = user_data.get("quiz", [])
    total = user_data.get("total", 0)

    if idx >= total:
        score = user_data.get("score", 0)
        per = int((score / total) * 100) if total > 0 else 0
        remaining = len(PROCESSED_DATA["direct"]) - len(ASKED_IDS)
        res = (
            f"🎉 **क्विज़ पूरा हुआ!**\n\n"
            f"✅ सही उत्तर: {score} / {total}\n"
            f"📊 स्कोर: {per}%\n"
            f"📚 शेष नए सवाल: {remaining}"
        )
        await context.bot.send_message(chat_id, res, parse_mode="Markdown")
        user_data["busy"] = False
        return

    q = quiz[idx]
    q_text = f"Q{idx + 1}/{total}. {q['question']}"
    options = q['options']
    correct_id = q['answer']

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q_text[:300],  # Max limit for telegram question text
        options=[opt[:100] for opt in options], # Max limit for options text
        type=Poll.QUIZ,
        correct_option_id=correct_id,
        is_anonymous=False
    )

    user_data["idx"] = idx + 1
    POLL_TRACKER[msg.poll.id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "correct_option_id": correct_id
    }

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id

    if poll_id not in POLL_TRACKER:
        return

    tracker = POLL_TRACKER.pop(poll_id)
    user_id = tracker["user_id"]
    chat_id = tracker["chat_id"]
    correct_option_id = tracker["correct_option_id"]

    if not poll_answer.option_ids:
        return

    selected = poll_answer.option_ids[0]
    user_data = context.application.user_data.get(user_id)

    if user_data and user_data.get("busy"):
        if selected == correct_option_id:
            user_data["score"] += 1
        await send_next_quiz(context, chat_id, user_id)

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("quiz", quiz_cmd))
    ptb_app.add_handler(CommandHandler("statement", statement_cmd))
    ptb_app.add_handler(CommandHandler("twisted", twisted_cmd))
    ptb_app.add_handler(CommandHandler("reset", reset_cmd))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_questions_file))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))

    await ptb_app.initialize()
    await ptb_app.start()

    webhook_url = f"{RENDER_URL}/{TOKEN}"
    await ptb_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)

    web_app = web.Application()

    async def telegram_webhook(request):
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception as e:
            logger.error(f"Error: {e}")
        return web.Response(text="OK")

    async def health_check(request):
        return web.Response(text="Bot Alive")

    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)
    web_app.router.add_get("/", health_check)

    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
