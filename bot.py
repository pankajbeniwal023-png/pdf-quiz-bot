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

# मास्टर प्रश्नों का डेटाबेस (मेमोरी में)
QUESTION_BANK = []
POLL_TRACKER = {}

# --- Gemini Rephraser Engine ---
async def rephrase_question_with_ai(original_q: dict, mode: str):
    """
    यह फ़ंक्शन ओरिजिनल सवाल को बिना उसका उत्तर बदले
    अलग-अलग लॉजिकल स्टाइल में रीफ़्रेम करेगा।
    """
    prompt = f"""
तुम एक बहुत ही सख्त परीक्षा विशेषज्ञ हो। 
नीचे एक सामान्य ज्ञान का ओरिजिनल प्रश्न, विकल्प और सही उत्तर का इंडेक्स दिया गया है:

Original Question: {json.dumps(original_q, ensure_ascii=False)}

तुम्हें इस सवाल का मुख्य कांसेप्ट (Concept) वही रखना है, लेकिन मोड '{mode}' के अनुसार इसे दोबारा लिखना है:

**नियम:**
1. **Mode 'twisted':** प्रश्न की भाषा थोड़ी घुमावदार और कठिन बनाओ ताकि विद्यार्थी को रटना न पड़े, बल्कि सोचना पड़े।
2. **Mode 'statement':** प्रश्न को कथन और कारण (Statement 1 और Statement 2) के रूप में बदलो।
3. **सही उत्तर बदलनी नहीं चाहिए:** जो विकल्प सही है, रीफ़्रेम होने के बाद भी वही विकल्प सही रहना चाहिए।
4. **आउटपुट:** केवल और केवल शुद्ध JSON ऑब्जेक्ट दो।

JSON Format:
{{
  "question": "नया घुमावदार प्रश्न?",
  "options": ["विकल्प 1", "विकल्प 2", "विकल्प 3", "विकल्प 4"],
  "answer": {original_q['answer']}
}}
"""

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.8,
            ),
        )
        data = json.loads(response.text.strip())
        return data
    except Exception as e:
        logger.error(f"Rephrase Error: {e}")
        # अगर AI फ़ेल होता है तो ओरिजिनल सवाल ही रिटर्न कर देगा (No Breakage)
        return original_q

# --- बॉट कमांड्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 **रटने के बजाय समझने वाला Quiz Bot**\n\n"
        "1. सबसे पहले अपनी **JSON फ़ाइल** मुझे भेजें (जिसमें प्रश्न का डेटा हो)।\n"
        "2. फिर नीचे दी गई कमांड्स का उपयोग करें:\n\n"
        "📌 `/quiz 10` - सामान्य पैटर्न के 10 सवाल\n"
        "🔄 `/twisted 10` - घुमावदार/लॉजिकल सवाल (AI Rephrased)\n"
        "📝 `/statement 10` - कथन एवं कारण वाले सवाल"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# JSON फ़ाइल प्राप्त करने का हैंडलर
async def handle_json_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global QUESTION_BANK
    doc = update.message.document
    if not doc.file_name.lower().endswith('.json'):
        return await update.message.reply_text("❌ कृपया केवल .json फ़ाइल भेजें।")

    msg = await update.message.reply_text("📥 JSON लोड हो रही है...")
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        if isinstance(data, list) and len(data) > 0:
            QUESTION_BANK = data
            await msg.edit_text(f"✅ **सफलतापूर्वक {len(QUESTION_BANK)} सवाल लोड हो गए!**\n\nअब अभ्यास शुरू करने के लिए `/twisted 5` या `/quiz 10` टाइप करें।", parse_mode="Markdown")
        else:
            await msg.edit_text("❌ JSON में प्रश्नों का प्रारूप सही नहीं है।")
    except Exception as e:
        logger.error(f"JSON Error: {e}")
        await msg.edit_text("❌ JSON फ़ाइल पढ़ने में त्रुटि हुई।")

# क्विज़ कमांड्स
async def start_quiz_session(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global QUESTION_BANK
    if not QUESTION_BANK:
        return await update.message.reply_text("❌ पहले अपनी Master JSON फ़ाइल बॉट को भेजें!")

    # यूजर ने कितने सवाल माँगे हैं (By Default 5)
    count = 5
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])

    selected_raw = random.sample(QUESTION_BANK, min(count, len(QUESTION_BANK)))
    
    msg = await update.message.reply_text(f"⚡ AI आपके प्रश्नों को `{mode.upper()}` स्टाइल में तैयार कर रहा है... ⏳", parse_mode="Markdown")

    processed_questions = []
    for q in selected_raw:
        if mode in ['twisted', 'statement']:
            rephrased = await rephrase_question_with_ai(q, mode)
            processed_questions.append(rephrased)
        else:
            processed_questions.append(q)

    await msg.delete()

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    context.user_data.clear()
    context.user_data.update({
        "quiz": processed_questions,
        "idx": 0,
        "score": 0,
        "total": len(processed_questions),
        "busy": True
    })

    await send_next_quiz(context, chat_id, user_id)

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="direct")

async def twisted_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="twisted")

async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="statement")

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
        res = f"🎉 **अभ्यास समाप्त!**\n\n✅ सही उत्तर: {score} / {total}\n📊 आपका स्कोर: {per}%"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown")
        user_data["busy"] = False
        return

    q = quiz[idx]
    q_text = f"Q{idx + 1}/{total}. {q['question']}"
    options = q['options']
    correct_id = q['answer']

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q_text,
        options=options,
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

# --- AIOHTTP Server & Webhook ---
async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("quiz", quiz_cmd))
    ptb_app.add_handler(CommandHandler("twisted", twisted_cmd))
    ptb_app.add_handler(CommandHandler("statement", statement_cmd))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_json_file))
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
