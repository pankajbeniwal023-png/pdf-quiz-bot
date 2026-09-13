import os
import json
import logging
import asyncio
import random
from aiohttp import web, ClientSession
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler,
    CallbackQueryHandler,
    ContextTypes
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL")
DRIVE_FILE_ID = os.environ.get("DRIVE_FILE_ID")

# Global Storage
PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
ASKED_IDS = set()
POLL_TRACKER = {}

async def fetch_data_from_google_drive():
    """Fetch JSON data from Google Drive public link with zero API lag"""
    global PROCESSED_DATA, ASKED_IDS
    if not DRIVE_FILE_ID:
        logger.error("DRIVE_FILE_ID Environment variable not set!")
        return False
        
    url = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    try:
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    text_data = await resp.text()
                    raw_json = json.loads(text_data.strip())
                    
                    PROCESSED_DATA["direct"] = [item["direct"] for item in raw_json if "direct" in item]
                    PROCESSED_DATA["statement"] = [item["statement"] for item in raw_json if "statement" in item]
                    PROCESSED_DATA["twisted"] = [item["twisted"] for item in raw_json if "twisted" in item]
                    
                    ASKED_IDS.clear()
                    logger.info(f"Successfully loaded {len(raw_json)} questions from Google Drive.")
                    return True
                else:
                    logger.error(f"Failed to fetch Google Drive file. Status code: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"Drive Sync Error: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Auto-load data if empty
    if not PROCESSED_DATA["direct"]:
        await fetch_data_from_google_drive()
        
    keyboard = [
        [
            InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("🔄 Drive Sync", callback_data="mode_sync")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        "🧠 **Google Drive Integrated Quiz Bot**\n\n"
        "1. गूगल ड्राइव से सभी प्रश्नों के कथन-कारण और Twisted वर्ज़न सिंक हो चुके हैं।\n"
        "2. नीचे दिए गए बटन पर क्लिक करें। **माइक्रो-सेकंड** में हर बार नया घुमावदार सवाल मिलेगा!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mode_reset":
        global ASKED_IDS
        ASKED_IDS.clear()
        return await query.message.reply_text("🧹 **पूछे गए सवालों की हिस्ट्री रीसेट हो गई है!**")

    if query.data == "mode_sync":
        success = await fetch_data_from_google_drive()
        if success:
            return await query.message.reply_text(f"✅ **Google Drive से {len(PROCESSED_DATA['direct'])} नए सवाल सिंक हो गए हैं!**")
        else:
            return await query.message.reply_text("❌ Google Drive से फ़ाइल सिंक करने में समस्या आई। File ID जांचें।")

    mode_map = {"mode_direct": "direct", "mode_statement": "statement", "mode_twisted": "twisted"}
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(query.message.chat_id, query.from_user.id, context, mode=selected_mode)

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global PROCESSED_DATA, ASKED_IDS
    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        await fetch_data_from_google_drive()
        bank = PROCESSED_DATA.get(mode, [])
        if not bank:
            return await context.bot.send_message(chat_id, "❌ Google Drive में सवाल नहीं मिले! पहले फ़ाइल अपलोड करें।")

    unasked_indices = [i for i in range(len(bank)) if i not in ASKED_IDS]

    if not unasked_indices:
        keyboard = [[InlineKeyboardButton("🧹 रीसेट करें", callback_data="mode_reset")]]
        return await context.bot.send_message(
            chat_id,
            "🎉 **सभी सवाल समाप्त हो चुके हैं!**\nदोबारा शुरू करने के लिए रीसेट दबाएं।",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    selected_indices = random.sample(unasked_indices, min(5, len(unasked_indices)))
    
    session_questions = []
    for idx in selected_indices:
        ASKED_IDS.add(idx)
        session_questions.append(bank[idx])

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
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Statement Mode", callback_data="mode_statement"),
                InlineKeyboardButton("🔄 Twisted Mode", callback_data="mode_twisted")
            ]
        ]
        res = f"🎉 **क्विज़ पूरा हुआ!**\n\n✅ सही उत्तर: {score}/{total}\n📊 स्कोर: {per}%"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        user_data["busy"] = False
        return

    q = quiz[idx]
    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Q{idx + 1}/{total}. {q['question']}"[:300],
        options=[opt[:100] for opt in q['options']],
        type=Poll.QUIZ,
        correct_option_id=q['answer'],
        is_anonymous=False
    )

    user_data["idx"] = idx + 1
    POLL_TRACKER[msg.poll.id] = {"user_id": user_id, "chat_id": chat_id, "correct_option_id": q['answer']}

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id

    if poll_id not in POLL_TRACKER:
        return

    tracker = POLL_TRACKER.pop(poll_id)
    user_id = tracker["user_id"]
    chat_id = tracker["chat_id"]

    if poll_answer.option_ids and poll_answer.option_ids[0] == tracker["correct_option_id"]:
        user_data = context.application.user_data.get(user_id)
        if user_data:
            user_data["score"] += 1

    await send_next_quiz(context, chat_id, user_id)

async def sync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    success = await fetch_data_from_google_drive()
    if success:
        await update.message.reply_text(f"✅ **Google Drive से {len(PROCESSED_DATA['direct'])} नए सवाल सिंक हो गए!**")
    else:
        await update.message.reply_text("❌ सिंक करने में एरर आई।")

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("sync", sync_cmd))
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))

    await ptb_app.initialize()
    await ptb_app.start()

    # Initial Sync on Boot
    await fetch_data_from_google_drive()

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

    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)
    web_app.router.add_get("/", lambda r: web.Response(text="Bot Alive"))

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
