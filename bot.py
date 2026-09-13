import os
import json
import logging
import asyncio
import random
import traceback
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

PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
LAST_ERROR = "No error logged yet."
USER_ASKED_IDS = {}
POLL_TRACKER = {}
USER_SESSIONS = {}  # mappingproxy एरर से बचने के लिए स्वतंत्र सेशन डिक्शनरी

def parse_correct_answer(raw_answer, options):
    """'A','B','C','D', 1-based (1,2,3,4) या स्ट्रिंग को Telegram के 0-indexed int में बदलता है"""
    if raw_answer is None:
        return 0
    if isinstance(raw_answer, int):
        if 0 <= raw_answer < len(options):
            return raw_answer
        if 1 <= raw_answer <= len(options):
            return raw_answer - 1

    ans_str = str(raw_answer).strip().lower()
    letter_map = {'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4}
    if ans_str in letter_map and letter_map[ans_str] < len(options):
        return letter_map[ans_str]
    
    if ans_str.isdigit():
        val = int(ans_str)
        if 0 <= val < len(options):
            return val
        if 1 <= val <= len(options):
            return val - 1

    for idx, opt in enumerate(options):
        if str(opt).strip().lower() == ans_str:
            return idx
    return 0

async def fetch_data_from_google_drive():
    global PROCESSED_DATA, LAST_ERROR
    if not DRIVE_FILE_ID:
        LAST_ERROR = "DRIVE_FILE_ID environment variable missing."
        logger.error(LAST_ERROR)
        return False, LAST_ERROR
        
    url = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    text_data = await resp.text()
                    clean_text = text_data.strip().replace("```json", "").replace("```", "")
                    
                    try:
                        raw_data = json.loads(clean_text)
                    except Exception as json_err:
                        LAST_ERROR = f"JSON Parsing Error: {json_err}"
                        logger.error(LAST_ERROR)
                        return False, LAST_ERROR

                    direct_list, statement_list, twisted_list = [], [], []

                    if isinstance(raw_data, list):
                        for item in raw_data:
                            if isinstance(item, dict):
                                if "direct" in item: direct_list.append(item["direct"])
                                if "statement" in item: statement_list.append(item["statement"])
                                if "twisted" in item: twisted_list.append(item["twisted"])

                    PROCESSED_DATA["direct"] = direct_list
                    PROCESSED_DATA["statement"] = statement_list
                    PROCESSED_DATA["twisted"] = twisted_list
                    
                    total_count = len(direct_list) + len(statement_list) + len(twisted_list)
                    if total_count == 0:
                        LAST_ERROR = "Drive file fetched, but 0 questions parsed from JSON keys."
                        return False, LAST_ERROR
                    
                    LAST_ERROR = f"Loaded {len(direct_list)} direct, {len(statement_list)} statement, {len(twisted_list)} twisted."
                    logger.info(LAST_ERROR)
                    return True, LAST_ERROR
                else:
                    LAST_ERROR = f"Drive HTTP Error: {resp.status}."
                    logger.error(LAST_ERROR)
                    return False, LAST_ERROR
    except Exception as e:
        LAST_ERROR = f"Drive Fetch Exception: {str(e)}"
        logger.error(LAST_ERROR)
        return False, LAST_ERROR

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 मिक्स मोड (हर सवाल नया तरीका)", callback_data="mode_mix")
        ],
        [
            InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("🔄 Drive Sync", callback_data="mode_sync")
        ]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set(), "mix": set()}
    USER_SESSIONS[user_id] = {"busy": False}
    
    if not PROCESSED_DATA["direct"]:
        await fetch_data_from_google_drive()
        
    msg = "🧠 **Quiz Bot Ready!**\n\nअपनी पसंद का मोड चुनें और खेलना शुरू करें:"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def debug_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sample = "None"
    if PROCESSED_DATA["direct"]:
        sample = json.dumps(PROCESSED_DATA["direct"][0], ensure_ascii=False)[:300]

    status_msg = (
        f"🛠 **Debug Report:**\n\n"
        f"• **Direct Questions:** {len(PROCESSED_DATA['direct'])}\n"
        f"• **Statement Questions:** {len(PROCESSED_DATA['statement'])}\n"
        f"• **Twisted Questions:** {len(PROCESSED_DATA['twisted'])}\n"
        f"• **Last Log:** `{LAST_ERROR}`\n\n"
        f"• **Sample Question 1:**\n`{sample}`"
    )
    await update.message.reply_text(status_msg, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        await query.answer()
    except Exception:
        pass

    if query.data == "mode_reset":
        USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set(), "mix": set()}
        USER_SESSIONS[user_id] = {"busy": False}
        return await query.message.reply_text("🧹 **हिस्ट्री रीसेट हो गई है!** नया मोड चुनें:", reply_markup=get_main_keyboard())

    if query.data == "mode_sync":
        success, err_msg = await fetch_data_from_google_drive()
        USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set(), "mix": set()}
        USER_SESSIONS[user_id] = {"busy": False}
        if success:
            return await query.message.reply_text(f"✅ **डेटा सिंक हो गया!**\n\n`{err_msg}`", parse_mode="Markdown")
        else:
            return await query.message.reply_text(f"❌ **Drive Sync एरर:**\n\n`{err_msg}`", parse_mode="Markdown")

    mode_map = {
        "mode_direct": "direct",
        "mode_statement": "statement",
        "mode_twisted": "twisted",
        "mode_mix": "mix"
    }
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(query.message.chat_id, user_id, context, mode=selected_mode)

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    try:
        if not PROCESSED_DATA["direct"]:
            success, err_msg = await fetch_data_from_google_drive()
            if not success:
                return await context.bot.send_message(chat_id, f"❌ **डेटा लोड एरर:**\n`{err_msg}`", parse_mode="Markdown")

        if user_id not in USER_ASKED_IDS:
            USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set(), "mix": set()}

        if mode == "mix":
            pool_size = max(len(PROCESSED_DATA["direct"]), len(PROCESSED_DATA["statement"]), len(PROCESSED_DATA["twisted"]))
        else:
            pool_size = len(PROCESSED_DATA.get(mode, []))

        if pool_size == 0:
            return await context.bot.send_message(chat_id, f"❌ '{mode}' मोड में कोई सवाल नहीं मिला। Drive Sync दबाएं या `/debug` चेक करें।")

        asked = USER_ASKED_IDS[user_id].get(mode, set())
        unasked_indices = [i for i in range(pool_size) if i not in asked]

        if not unasked_indices:
            keyboard = [[InlineKeyboardButton("🧹 रीसेट करके पुनः खेलें", callback_data="mode_reset")]]
            return await context.bot.send_message(
                chat_id,
                "🎉 **इस फ़ाइल के सभी सवाल समाप्त हो चुके हैं!**\n\nफिर से खेलने के लिए रीसेट बटन दबाएं:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        selected_indices = random.sample(unasked_indices, min(5, len(unasked_indices)))
        
        session_questions = []
        for idx in selected_indices:
            if mode == "mix":
                possible_types = [t for t in ["direct", "statement", "twisted"] if idx < len(PROCESSED_DATA[t])]
                chosen_type = random.choice(possible_types) if possible_types else "direct"
                q_obj = PROCESSED_DATA[chosen_type][idx]
            else:
                q_obj = PROCESSED_DATA[mode][idx]
            
            session_questions.append((idx, q_obj))

        # USER_SESSIONS का उपयोग (mappingproxy एरर ठीक)
        USER_SESSIONS[user_id] = {
            "mode": mode,
            "quiz": session_questions,
            "idx": 0,
            "score": 0,
            "total": len(session_questions),
            "busy": True
        }

        await send_next_quiz(context, chat_id, user_id)

    except Exception as e:
        logger.error(f"Error starting session: {e}\n{traceback.format_exc()}")
        await context.bot.send_message(chat_id, f"❌ **सत्र शुरू करने में एरर:**\n`{str(e)}`")

async def send_next_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    user_data = USER_SESSIONS.get(user_id)
    if not user_data or not user_data.get("busy"):
        return

    idx = user_data.get("idx", 0)
    quiz = user_data.get("quiz", [])
    total = user_data.get("total", 0)
    mode = user_data.get("mode", "direct")

    if idx >= total:
        score = user_data.get("score", 0)
        per = int((score / total) * 100) if total > 0 else 0
        res = f"🎉 **क्विज़ समाप्त!**\n\n✅ सही उत्तर: {score}/{total}\n📊 आपका स्कोर: {per}%"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=get_main_keyboard())
        user_data["busy"] = False
        return

    question_index, q = quiz[idx]

    try:
        raw_q = q.get('question') or q.get('q') or "सवाल उपलब्ध नहीं है"
        clean_question = str(raw_q).replace("|\\n", "\n").replace("\\n", "\n").replace("|", "").strip()
        poll_question = f"Q{idx + 1}/{total}. {clean_question}"[:295]

        raw_options = q.get('options') or q.get('choices') or []
        if isinstance(raw_options, dict):
            poll_options = [str(v).strip()[:95] for v in raw_options.values() if str(v).strip()]
        elif isinstance(raw_options, list):
            poll_options = [str(opt).strip()[:95] for opt in raw_options if str(opt).strip()]
        else:
            poll_options = []

        while len(poll_options) < 2:
            poll_options.append(f"विकल्प {len(poll_options)+1}")

        poll_options = poll_options[:10]

        raw_answer = q.get('answer') if q.get('answer') is not None else q.get('correct')
        correct_id = parse_correct_answer(raw_answer, poll_options)

        msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=poll_question,
            options=poll_options,
            type=Poll.QUIZ,
            correct_option_id=correct_id,
            is_anonymous=False
        )

        USER_ASKED_IDS[user_id][mode].add(question_index)
        user_data["idx"] = idx + 1
        POLL_TRACKER[msg.poll.id] = {"user_id": user_id, "chat_id": chat_id, "correct_option_id": correct_id}

    except Exception as e:
        err_detail = traceback.format_exc()
        logger.error(f"Poll Send Failed on Q{idx+1}: {err_detail}")
        await context.bot.send_message(
            chat_id, 
            f"⚠️ **सवाल Q{idx+1} भेजने में एरर आया:**\n`{str(e)}`"
        )
        user_data["idx"] = idx + 1
        await send_next_quiz(context, chat_id, user_id)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id

    if poll_id not in POLL_TRACKER:
        return

    tracker = POLL_TRACKER.pop(poll_id)
    user_id = tracker["user_id"]
    chat_id = tracker["chat_id"]

    if poll_answer.option_ids and poll_answer.option_ids[0] == tracker["correct_option_id"]:
        user_data = USER_SESSIONS.get(user_id)
        if user_data:
            user_data["score"] += 1

    await send_next_quiz(context, chat_id, user_id)

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("reset", start))
    ptb_app.add_handler(CommandHandler("debug", debug_status))
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
            logger.error(f"Error handling update: {e}")
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
