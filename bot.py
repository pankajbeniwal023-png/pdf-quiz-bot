import os
import json
import logging
import asyncio
import random
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler,
    MessageHandler,
    CallbackQueryHandler,
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

# Global Storage
PROCESSED_DATA = {
    "direct": [],
    "statement": [],
    "twisted": []
}
ASKED_IDS = set()
POLL_TRACKER = {}

# Bulk Generator Prompt
async def process_all_questions_in_bulk(raw_questions: list):
    prompt = f"""
You are an expert competitive exam question creator. 
Convert each provided raw question/fact into 3 distinct variation formats:

1. "direct": Standard fact-based question.
2. "statement": Strict Assertion-Reason style in Hindi.
   - Format Question: "कथन (A): ... \nकारण (R): ..."
   - Standard Options MUST be:
     0: कथन (A) और कारण (R) दोनों सही हैं और (R), (A) की सही व्याख्या है।
     1: कथन (A) और कारण (R) दोनों सही हैं लेकिन (R), (A) की सही व्याख्या नहीं है।
     2: कथन (A) सही है लेकिन कारण (R) गलत है।
     3: कथन (A) गलत है लेकिन कारण (R) सही है।
3. "twisted": Rephrase the question analytically so the candidate has to think conceptually rather than relying on rote memory.

Input Questions JSON:
{json.dumps(raw_questions, ensure_ascii=False)}

Rules:
- Output STRICT VALID JSON array with this schema:
[
  {{
    "id": 0,
    "direct": {{"question": "...", "options": ["...", "...", "...", "..."], "answer": 0}},
    "statement": {{"question": "...", "options": ["...", "...", "...", "..."], "answer": 0}},
    "twisted": {{"question": "...", "options": ["...", "...", "...", "..."], "answer": 0}}
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
                temperature=0.6,
            ),
        )
        parsed = json.loads(response.text.strip())
        return parsed
    except Exception as e:
        logger.error(f"Bulk Generation Error: {e}")
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
    keyboard = [
        [
            InlineKeyboardButton("🎯 डायरेक्ट क्विज़", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण (Statement)", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("🧹 रीसेट डेटा", callback_data="mode_reset")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        "🧠 **Ultra-Fast Concept Building Quiz Bot**\n\n"
        "1. अपनी `.json` या `.txt` फ़ाइल भेजें।\n"
        "2. फ़ाइल अपलोड होते ही AI सभी प्रश्नों के वेरिएशन्स तैयार कर लेगा।\n"
        "3. बटन पर क्लिक करके **माइक्रो-सेकंड** में क्विज़ मोड बदलें!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_questions_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PROCESSED_DATA, ASKED_IDS
    doc = update.message.document
    file_name = doc.file_name.lower()
    
    if not (file_name.endswith('.json') or file_name.endswith('.txt')):
        return await update.message.reply_text("❌ केवल .json या .txt फ़ाइल अपलोड करें।")

    status_msg = await update.message.reply_text("📥 फ़ाइल मिल गई! AI कथन-कारण और लॉजिकल प्रश्न तैयार कर रहा है... ⚡")
    
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
            
            keyboard = [
                [InlineKeyboardButton("📝 कथन-कारण टेस्ट (Statement)", callback_data="mode_statement")],
                [InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted")],
                [InlineKeyboardButton("🎯 डायरेक्ट क्विज़ (Direct)", callback_data="mode_direct")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await status_msg.edit_text(
                f"✅ **{len(data)} प्रश्न सफलतापूर्वक लोड हो गए!**\n\n"
                "⚡ नीचे दिए गए बटन पर क्लिक करके टेस्ट शुरू करें:", 
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await status_msg.edit_text("❌ फ़ाइल में सही JSON प्रारूप नहीं है।")
    except Exception as e:
        logger.error(f"File handling error: {e}")
        await status_msg.edit_text("❌ फ़ाइल प्रोसेस करने में त्रुटि हुई। फ़ाइल फ़ॉर्मैट जांचें।")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mode_reset":
        global ASKED_IDS
        ASKED_IDS.clear()
        return await query.message.reply_text("🧹 **सभी पूछे गए प्रश्नों की हिस्ट्री रीसेट कर दी गई है!**")

    mode_map = {
        "mode_direct": "direct",
        "mode_statement": "statement",
        "mode_twisted": "twisted"
    }

    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(
            chat_id=query.message.chat_id,
            user_id=query.from_user.id,
            context=context,
            mode=selected_mode
        )

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update.effective_chat.id, update.effective_user.id, context, mode="direct")

async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update.effective_chat.id, update.effective_user.id, context, mode="statement")

async def twisted_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update.effective_chat.id, update.effective_user.id, context, mode="twisted")

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global PROCESSED_DATA, ASKED_IDS
    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        return await context.bot.send_message(chat_id, "❌ कृपया पहले प्रश्नों वाली फ़ाइल अपलोड करें!")

    unasked_indices = [i for i in range(len(bank)) if i not in ASKED_IDS]

    if not unasked_indices:
        keyboard = [[InlineKeyboardButton("🧹 हिस्ट्री रीसेट करें", callback_data="mode_reset")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        return await context.bot.send_message(
            chat_id,
            "🎉 **सभी प्रश्न समाप्त हो चुके हैं!**\nपुनः शुरू करने के लिए रीसेट बटन दबाएं।",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    count = 5
    selected_indices = random.sample(unasked_indices, min(count, len(unasked_indices)))
    
    session_questions = []
    for idx in selected_indices:
        ASKED_IDS.add(idx)
        session_questions.append(bank[idx])

    # Save state under application user_data properly
    context.application.user_data[user_id] = {
        "quiz": session_questions,
        "idx": 0,
        "score": 0,
        "total": len(session_questions),
        "busy": True
    }

    await send_next_quiz(context, chat_id, user_id)

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
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Statement Mode", callback_data="mode_statement"),
                InlineKeyboardButton("🔄 Twisted Mode", callback_data="mode_twisted")
            ],
            [InlineKeyboardButton("🧹 रीसेट", callback_data="mode_reset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        res = (
            f"🎉 **क्विज़ पूरा हुआ!**\n\n"
            f"✅ सही उत्तर: {score} / {total}\n"
            f"📊 स्कोर: {per}%\n"
            f"📚 शेष नए सवाल: {remaining}"
        )
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=reply_markup)
        user_data["busy"] = False
        return

    q = quiz[idx]
    q_text = f"Q{idx + 1}/{total}. {q['question']}"
    options = q['options']
    correct_id = q['answer']

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q_text[:300],
        options=[opt[:100] for opt in options],
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

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ASKED_IDS
    ASKED_IDS.clear()
    await update.message.reply_text("🧹 **हिस्ट्री रीसेट कर दी गई है!**")

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("quiz", quiz_cmd))
    ptb_app.add_handler(CommandHandler("statement", statement_cmd))
    ptb_app.add_handler(CommandHandler("twisted", twisted_cmd))
    ptb_app.add_handler(CommandHandler("reset", reset_cmd))
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
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
