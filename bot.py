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
USER_ASKED_IDS = {}  # Per-user asked questions set
POLL_TRACKER = {}

async def fetch_data_from_google_drive():
    """Fetch JSON data from Google Drive public link"""
    global PROCESSED_DATA
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
                    
                    logger.info(f"Loaded {len(raw_json)} questions from Google Drive.")
                    return True
                else:
                    logger.error(f"Failed to fetch Google Drive file. Status code: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"Drive Sync Error: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "1. गूगल ड्राइव से सवाल 100% ऑटो-सिंक हो चुके हैं।\n"
        "2. बटन दबाते ही **0.1 सेकंड** में नया घुमावदार सवाल आएगा!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Real Reset Handler for /reset command"""
    user_id = update.effective_user.id
    USER_ASKED_IDS[user_id] = set()
    
    keyboard = [
        [
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement"),
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted")
        ],
        [InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct")]
    ]
    await update.message.reply_text(
        "🧹 **आपकी हिस्ट्री सफलता पूर्वक रीसेट हो गई है!**\n\nअब नया टेस्ट शुरू करने के लिए मोड चुनें:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "mode_reset":
        USER_ASKED_IDS[user_id] = set()
        keyboard = [
            [
                InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement"),
                InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted")
            ]
        ]
        return await query.message.reply_text(
            "🧹 **आपकी हिस्ट्री रीसेट हो गई है!**\nनीचे बटन दबाकर क्विज़ शुरू करें:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    if query.data == "mode_sync":
        success = await fetch_data_from_google_drive()
        if success:
            USER_ASKED_IDS[user_id] = set()
            return await query.message.reply_text(f"✅ **Google Drive से {len(PROCESSED_DATA['direct'])} नए सवाल सिंक हो गए हैं!**")
        else:
            return await query.message.reply_text("❌ Google Drive से फ़ाइल सिंक करने में समस्या आई। File ID जांचें।")

    mode_map = {"mode_direct": "direct", "mode_statement": "statement", "mode_twisted": "twisted"}
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(query.message.chat_id, user_id, context, mode=selected_mode)

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global PROCESSED_DATA, USER_ASKED_IDS
    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        await fetch_data_from_google_drive()
        bank = PROCESSED_DATA.get(mode, [])
        if not bank:
            return await context.bot.send_message(chat_id, "❌ Google Drive में सवाल नहीं मिले! फ़ाइल सही अपलोड करें।")

    if user_id not in USER_ASKED_IDS:
        USER_ASKED_IDS[user_id] = set()

    asked = USER_ASKED_IDS[user_id]
    unasked_indices = [i for i in range(len(bank)) if i not in asked]

    if not unasked_indices:
        keyboard = [[InlineKeyboardButton("🧹 रीसेट करें", callback_data="mode_reset")]]
        return await context.bot.send_message(
            chat_id,
            "🎉 **इस फ़ाइल के सभी सवाल समाप्त हो चुके हैं!**\n\nदोबारा खेलने के लिए नीचे **रीसेट करें** बटन दबाएं।",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    selected_indices = random.sample(unasked_indices, min(5, len(unasked_indices)))
    
    session_questions = []
    for idx in selected_indices:
        USER_ASKED_IDS[user_id].add(idx)
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
            ],
            [InlineKeyboardButton("🎯 Direct Mode", callback_data="mode_direct")]
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

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("reset", reset_cmd))
    ptb_app.add_handler(CommandHandler("sync", reset_cmd))
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))

    await ptb_app.initialize()
    await ptb_app.start()

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
