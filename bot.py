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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
USER_ASKED_IDS = {}
POLL_TRACKER = {}

async def fetch_data_from_google_drive():
    global PROCESSED_DATA
    if not DRIVE_FILE_ID:
        return False
        
    url = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    text_data = await resp.text()
                    clean_text = text_data.strip().replace("```json", "").replace("```", "")
                    raw_data = json.loads(clean_text)
                    
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
                    return True
                return False
    except Exception as e:
        logger.error(f"Drive Exception: {e}")
        return False

async def generate_ai_question(mode: str):
    """Google Gemini AI से डायनामिक प्रश्न जनरेट करने का फंक्शन"""
    if not GEMINI_API_KEY:
        return None

    mode_prompts = {
        "direct": "राजस्थान सामान्य ज्ञान (Rajasthan GK) का एक सीधा बहुविकल्पीय प्रश्न (MCQ) बनाएं।",
        "statement": "राजस्थान सामान्य ज्ञान पर आधारित एक कठिन 'कथन (A) और कारण (R)' प्रकार का MCQ बनाएं।",
        "twisted": "राजस्थान सामान्य ज्ञान पर आधारित एक घुमावदार, स्थिति-आधारित (Case-study type twisted) MCQ बनाएं।"
    }

    prompt = (
        f"{mode_prompts.get(mode, 'Rajasthan GK MCQ')} "
        f"Strictly return ONLY a valid JSON object without markdown syntax using this exact format:\n"
        '{"question": "प्रश्न यहाँ", "options": ["विकल्प 1", "विकल्प 2", "विकल्प 3", "विकल्प 4"], "answer": 0}'
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}

    try:
        async with ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    raw_text = res_json['candidates'][0]['content']['parts'][0]['text']
                    clean_text = raw_text.strip().replace("```json", "").replace("```", "").strip()
                    return json.loads(clean_text)
    except Exception as e:
        logger.error(f"Gemini AI API Error: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set()}
    
    if not PROCESSED_DATA["direct"]:
        await fetch_data_from_google_drive()
        
    keyboard = [
        [
            InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("🤖 AI अनलिमिटेड मोड", callback_data="mode_ai")
        ],
        [InlineKeyboardButton("🔄 Drive Sync", callback_data="mode_sync")]
    ]
    
    msg = "🧠 **Quiz Bot Ready!**\n\nनीचे दिए गए बटन पर क्लिक करके खेलना शुरू करें:"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        await query.answer()
    except Exception:
        pass

    if query.data == "mode_reset":
        USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set()}
        keyboard = [
            [
                InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
                InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
            ],
            [
                InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
                InlineKeyboardButton("🤖 AI अनलिमिटेड मोड", callback_data="mode_ai")
            ]
        ]
        return await query.message.reply_text("🧹 **हिस्ट्री रीसेट हो गई है!** नया मोड चुनें:", reply_markup=InlineKeyboardMarkup(keyboard))

    if query.data == "mode_sync":
        success = await fetch_data_from_google_drive()
        USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set()}
        if success:
            return await query.message.reply_text("✅ **Google Drive से नया डेटा सिंक हो गया!**")
        else:
            return await query.message.reply_text("❌ Drive Sync फेल हो गया। Permissions चेक करें।")

    mode_map = {
        "mode_direct": "direct", 
        "mode_statement": "statement", 
        "mode_twisted": "twisted",
        "mode_ai": "ai"
    }
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(query.message.chat_id, user_id, context, mode=selected_mode)

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    if mode == "ai":
        # AI Mode logic
        ai_question = await generate_ai_question("twisted")
        if ai_question:
            context.application.user_data[user_id] = {
                "quiz": [ai_question],
                "idx": 0,
                "score": 0,
                "total": 1,
                "busy": True,
                "is_ai": True
            }
            return await send_next_quiz(context, chat_id, user_id)
        else:
            return await context.bot.send_message(chat_id, "⚠️ AI से प्रश्न जनरेट नहीं हो सका। कृपया GEMINI_API_KEY की जाँच करें।")

    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        await fetch_data_from_google_drive()
        bank = PROCESSED_DATA.get(mode, [])

    if not bank:
        # Fallback to AI if Drive file fails
        ai_q = await generate_ai_question(mode)
        if ai_q:
            bank = [ai_q]
        else:
            keyboard = [[InlineKeyboardButton("🔄 Drive Sync", callback_data="mode_sync")]]
            return await context.bot.send_message(
                chat_id,
                "❌ **Google Drive से डेटा लोड नहीं हो सका!**\n\nकृपया ड्राइव फ़ाइल का एक्सेस 'Anyone with the link' पर सेट करें और री-सिंक करें:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    if user_id not in USER_ASKED_IDS:
        USER_ASKED_IDS[user_id] = {"direct": set(), "statement": set(), "twisted": set()}

    asked = USER_ASKED_IDS[user_id].get(mode, set())
    unasked_indices = [i for i in range(len(bank)) if i not in asked]

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
        USER_ASKED_IDS[user_id][mode].add(idx)
        session_questions.append(bank[idx])

    context.application.user_data[user_id] = {
        "quiz": session_questions,
        "idx": 0,
        "score": 0,
        "total": len(session_questions),
        "busy": True,
        "is_ai": False
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
            [
                InlineKeyboardButton("🎯 Direct Mode", callback_data="mode_direct"),
                InlineKeyboardButton("🤖 AI मोड", callback_data="mode_ai")
            ]
        ]
        res = f"🎉 **क्विज़ समाप्त!**\n\n✅ सही: {score}/{total}\n📊 स्कोर: {per}%"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        user_data["busy"] = False
        return

    q = quiz[idx]
    clean_question = str(q['question']).replace("|\\n", "\n").replace("\\n", "\n").replace("|", "")

    poll_question = f"Q{idx + 1}/{total}. {clean_question}"[:295]
    poll_options = [str(opt)[:95] for opt in q['options']]

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=poll_question,
        options=poll_options,
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
    ptb_app.add_handler(CommandHandler("reset", start))
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
