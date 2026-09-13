import os
import json
import logging
import asyncio
import random
import traceback
from aiohttp import web, ClientSession
import google.generativeai as genai
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_URL")
DRIVE_FILE_ID = os.environ.get("DRIVE_FILE_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini AI सेटअप
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    ai_model = None

# यूज़र के नोट्स और सेशन स्टोर करने के लिए
USER_STUDY_MATERIAL = {}  # {user_id: {"type": "pdf/text/image", "data": bytes/str}}
USER_SESSIONS = {}
POLL_TRACKER = {}

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 AI मिक्स मोड (हर बार नया सवाल)", callback_data="mode_mix")
        ],
        [
            InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("📥 Drive से PDF सिंक", callback_data="sync_drive_pdf")
        ]
    ])

async def generate_questions_with_ai(user_id: int, mode: str, count: int = 5):
    """Gemini AI से सीधे रंगीन नोट्स/PDF से नए सवाल जनरेट करवाता है"""
    material = USER_STUDY_MATERIAL.get(user_id)
    if not material:
        return None, "⚠️ कोई नोट्स/PDF अपलोड नहीं है! कृपया अपनी रंगीन PDF या नोट्स की फोटो यहाँ चैट में भेजें, या Drive Sync दबाएं।"

    mode_prompts = {
        "direct": "सरल और सीधे बहुविकल्पीय सवाल (Direct MCQs) बनाएं।",
        "statement": "UPSC/RPSC स्तर के कथन-कारण (Statement 1, Statement 2 / Assertion-Reason) वाले सवाल बनाएं।",
        "twisted": "घुमावदार, व्यावहारिक और कॉन्सेप्ट की गहराई जांचने वाले (Twisted / Scenario based) सवाल बनाएं।",
        "mix": "मिश्रित सवाल बनाएं: कुछ डायरेक्ट, कुछ कथन-कारण और कुछ घुमावदार।"
    }

    prompt = f"""
    आप एक उच्च स्तरीय शिक्षक हैं। नीचे दिए गए स्टडी मटीरियल (नोट्स/किताब) को ध्यानपूर्वक पढ़ें।
    इस मटीरियल से {count} बिल्कुल नए और अलग प्रश्न तैयार करें।
    प्रश्नों का प्रकार: {mode_prompts.get(mode, mode_prompts['mix'])}

    नियम:
    1. सवाल और विकल्प हिंदी में होने चाहिए।
    2. हर सवाल के 4 स्पष्ट विकल्प हों।
    3. सवाल 280 अक्षरों से छोटा होना चाहिए और हर विकल्प 90 अक्षरों से छोटा।
    4. उत्तर को 0-indexed संख्या के रूप में दें (0 = पहला विकल्प, 1 = दूसरा विकल्प, 2 = तीसरा, 3 = चौथा)।
    5. केवल शुद्ध JSON फॉर्मेट में उत्तर दें, कोई अन्य फालतू टेक्स्ट न लिखें।

    JSON फॉर्मेट:
    [
      {{
        "question": "सवाल यहाँ...",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer": 0
      }}
    ]
    """

    try:
        content_parts = [prompt]
        if material["type"] == "bytes":
            content_parts.append({
                "mime_type": material["mime_type"],
                "data": material["data"]
            })
        else:
            content_parts.append(material["data"])

        response = ai_model.generate_content(
            content_parts,
            generation_config={"response_mime_type": "application/json"}
        )
        questions = json.loads(response.text.strip())
        return questions, None
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        return None, f"AI एरर: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_SESSIONS[user_id] = {"busy": False}
    
    msg = (
        "🧠 **AI Quiz Revision Bot Ready!**\n\n"
        "💡 **अब सवाल कभी रिपीट नहीं होंगे!**\n"
        "1. आप अपनी **रंगीन PDF, नोट्स की फोटो या टेक्स्ट** सीधे मुझे यहाँ भेज सकते हैं।\n"
        "2. या नीचे दिए गए **Drive से PDF सिंक** बटन को दबा सकते हैं।\n\n"
        "नीचे दिए गए किसी भी मोड पर क्लिक करके क्विज़ शुरू करें:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_document_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """जब आप बॉट में सीधे रंगीन PDF या नोट्स की फोटो भेजेंगे"""
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("📥 **मटीरियल प्राप्त हो रहा है, कृपया 5 सेकंड प्रतीक्षा करें...**")

    try:
        if update.message.document:
            doc = update.message.document
            file = await context.bot.get_file(doc.file_id)
            data = await file.download_as_bytearray()
            mime = doc.mime_type or "application/pdf"
            USER_STUDY_MATERIAL[user_id] = {"type": "bytes", "data": bytes(data), "mime_type": mime}
            await status_msg.edit_text("✅ **आपकी PDF सफलतापूर्वक लोड हो गई!**\nअब नीचे से मोड चुनें, AI तुरंत नए सवाल बनाएगा:", reply_markup=get_main_keyboard())
        
        elif update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            data = await file.download_as_bytearray()
            USER_STUDY_MATERIAL[user_id] = {"type": "bytes", "data": bytes(data), "mime_type": "image/jpeg"}
            await status_msg.edit_text("✅ **नोट्स की फ़ोटो लोड हो गई!**\nअब कोई भी मोड चुनकर नए सवाल खेलें:", reply_markup=get_main_keyboard())

    except Exception as e:
        await status_msg.edit_text(f"❌ फ़ाइल लोड करने में समस्या आई: {e}")

async def sync_drive_file(user_id: int):
    """Google Drive से सीधे PDF डाउनलोड करके AI में लोड करना"""
    if not DRIVE_FILE_ID:
        return False, "DRIVE_FILE_ID सेट नहीं है।"
    url = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=25) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    USER_STUDY_MATERIAL[user_id] = {
                        "type": "bytes",
                        "data": data,
                        "mime_type": "application/pdf"
                    }
                    return True, "Google Drive से PDF सिंक हो गई है!"
                else:
                    return False, f"Drive HTTP Code: {resp.status}"
    except Exception as e:
        return False, str(e)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "sync_drive_pdf":
        msg = await query.message.reply_text("🔄 **Drive से PDF डाउनलोड हो रही है...**")
        success, err = await sync_drive_file(user_id)
        if success:
            return await msg.edit_text("✅ **Drive PDF लोड हो गई!** अब मोड चुनें:", reply_markup=get_main_keyboard())
        else:
            return await msg.edit_text(f"❌ Drive सिंक विफल: {err}")

    mode_map = {
        "mode_direct": "direct",
        "mode_statement": "statement",
        "mode_twisted": "twisted",
        "mode_mix": "mix"
    }
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_ai_quiz(query.message.chat_id, user_id, context, mode=selected_mode)

async def start_ai_quiz(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    if not GEMINI_API_KEY:
        return await context.bot.send_message(chat_id, "❌ `GEMINI_API_KEY` सेट नहीं है! कृपया Render में API Key डालें।")

    load_msg = await context.bot.send_message(chat_id, "🤖 **AI आपके नोट्स से बिल्कुल नए सवाल तैयार कर रहा है... (5-10 सेकंड)**")

    # AI से नए सवाल जनरेट करवाएं
    questions, err = await generate_questions_with_ai(user_id, mode, count=5)
    if err or not questions:
        return await load_msg.edit_text(f"❌ सवाल नहीं बन पाए:\n`{err}`")

    await load_msg.delete()

    USER_SESSIONS[user_id] = {
        "quiz": questions,
        "idx": 0,
        "score": 0,
        "total": len(questions),
        "busy": True
    }

    await send_next_quiz(context, chat_id, user_id)

async def send_next_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    user_data = USER_SESSIONS.get(user_id)
    if not user_data or not user_data.get("busy"):
        return

    idx = user_data.get("idx", 0)
    quiz = user_data.get("quiz", [])
    total = user_data.get("total", 0)

    if idx >= total:
        score = user_data.get("score", 0)
        per = int((score / total) * 100) if total > 0 else 0
        res = f"🎉 **सत्र समाप्त!**\n\n✅ सही: {score}/{total}\n📊 स्कोर: {per}%\n\nफिर से नए सवाल खेलने के लिए कोई भी मोड चुनें:"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=get_main_keyboard())
        user_data["busy"] = False
        return

    q = quiz[idx]
    poll_question = f"Q{idx + 1}/{total}. {q['question']}"[:295]
    poll_options = [str(opt)[:95] for opt in q['options']][:4]
    
    # 0-indexed int सुनिश्चित करना
    correct_id = int(q.get('answer', 0))
    if correct_id >= len(poll_options):
        correct_id = 0

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=poll_question,
        options=poll_options,
        type=Poll.QUIZ,
        correct_option_id=correct_id,
        is_anonymous=False
    )

    user_data["idx"] = idx + 1
    POLL_TRACKER[msg.poll.id] = {"user_id": user_id, "chat_id": chat_id, "correct_option_id": correct_id}

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
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))
    # PDF या फोटो प्राप्त करने के लिए हैंडलर
    ptb_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document_or_photo))

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
