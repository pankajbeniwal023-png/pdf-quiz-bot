import os
import re
import io
import json
import logging
import asyncio
import random
import tempfile
import traceback
from aiohttp import web, ClientSession
import pypdf
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
RAW_DRIVE_ID = os.environ.get("DRIVE_FILE_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def clean_drive_id(raw_val):
    if not raw_val:
        return ""
    val = raw_val.strip()
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", val)
    if match:
        return match.group(1)
    match = re.search(r"id=([a-zA-Z0-9_-]+)", val)
    if match:
        return match.group(1)
    return val

DRIVE_FILE_ID = clean_drive_id(RAW_DRIVE_ID)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

USER_STUDY_MATERIAL = {}
USER_SESSIONS = {}
POLL_TRACKER = {}

def extract_text_from_pdf(pdf_bytes):
    """pypdf के जरिए PDF से पूरा टेक्स्ट सुरक्षित निकालना"""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        extracted = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                extracted.append(t)
        return "\n".join(extracted).strip()
    except Exception as e:
        logger.warning(f"pypdf extraction failed: {e}")
        return ""

def parse_correct_answer(raw_answer, options):
    """0-indexed सही विकल्प इंडेक्स निकालना"""
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

def get_best_available_model():
    """उपलब्ध मॉडल चुनना"""
    preferred_models = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-1.5-flash-latest"
    ]
    try:
        online_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for pref in preferred_models:
            for on_m in online_models:
                if pref in on_m:
                    return on_m
        if online_models:
            return online_models[0]
    except Exception as e:
        logger.warning(f"Model list fallback: {e}")
    
    return "models/gemini-3.8-flash"

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

async def sync_drive_file(user_id: int):
    """Google Drive से असली PDF डाउनलोड करना"""
    if not DRIVE_FILE_ID:
        return False, "DRIVE_FILE_ID सेट नहीं है।"

    urls = [
        f"https://drive.usercontent.google.com/download?id={DRIVE_FILE_ID}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}&confirm=t"
    ]

    for url in urls:
        try:
            async with ClientSession() as session:
                async with session.get(url, timeout=30, allow_redirects=True) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if data.startswith(b"<!DOCTYPE") or data.startswith(b"<html"):
                            continue
                        
                        USER_STUDY_MATERIAL[user_id] = {
                            "type": "bytes",
                            "data": data,
                            "mime_type": "application/pdf"
                        }
                        return True, "Google Drive से PDF लोड हो गई!"
        except Exception as e:
            logger.warning(f"Drive download failed on {url}: {e}")
            continue

    return False, "Drive से असली PDF नहीं मिली। कृपया Drive फ़ाइल को 'Anyone with link' (पब्लिक) करें, या सीधे इस चैट में अपनी PDF भेज दें!"

async def generate_questions_with_ai(user_id: int, mode: str, count: int = 5):
    material = USER_STUDY_MATERIAL.get(user_id)
    if not material:
        return None, "⚠️ कोई नोट्स/PDF लोड नहीं है!"

    mode_prompts = {
        "direct": "सरल और सीधे बहुविकल्पीय सवाल (Direct MCQs) बनाएं।",
        "statement": "UPSC/RPSC स्तर के कथन-कारण (Statement 1, Statement 2 / Assertion-Reason) वाले उच्च स्तरीय सवाल बनाएं।",
        "twisted": "घुमावदार, व्यावहारिक और कॉन्सेप्ट की गहराई जांचने वाले (Twisted / Case-Study based) सवाल बनाएं।",
        "mix": "मिश्रित सवाल बनाएं: कुछ डायरेक्ट, कुछ कथन-कारण और कुछ घुमावदार।"
    }

    prompt = f"""
    आप एक उच्च स्तरीय शिक्षक हैं। नीचे दिए गए स्टडी मटीरियल को ध्यानपूर्वक पढ़ें।
    इस मटीरियल से {count} बिल्कुल नए प्रश्न तैयार करें।
    प्रश्नों का प्रकार: {mode_prompts.get(mode, mode_prompts['mix'])}

    नियम:
    1. सवाल और विकल्प हिंदी में होने चाहिए।
    2. हर सवाल के 4 स्पष्ट विकल्प हों।
    3. सवाल 280 अक्षरों से छोटा और प्रत्येक विकल्प 90 अक्षरों से छोटा होना चाहिए।
    4. उत्तर को 0-indexed संख्या दें (0 = पहला, 1 = दूसरा, 2 = तीसरा, 3 = चौथा)।
    5. सिर्फ JSON एरे लौटाएं:

    [
      {{
        "question": "सवाल यहाँ...",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer": 0
      }}
    ]
    """

    content_parts = []
    temp_uploaded_file = None
    temp_local_path = None

    try:
        if material["mime_type"] == "application/pdf":
            # 1. पहले pypdf से टेक्स्ट निकालने की कोशिश (सुपर फास्ट और 400 एरर प्रूफ)
            extracted_text = extract_text_from_pdf(material["data"])
            if len(extracted_text) > 80:
                content_parts = [prompt, f"\n\n--- संदर्भ सामग्री ---\n{extracted_text[:40000]}"]
            else:
                # 2. अगर स्कैन्ड इमेज PDF है तो Google File API के जरिए अपलोड
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(material["data"])
                    temp_local_path = tmp.name
                
                temp_uploaded_file = genai.upload_file(path=temp_local_path, mime_type="application/pdf")
                content_parts = [temp_uploaded_file, prompt]

        elif material["mime_type"].startswith("image/"):
            content_parts = [
                prompt,
                {
                    "mime_type": material["mime_type"],
                    "data": material["data"]
                }
            ]
        else:
            content_parts = [prompt, str(material["data"])]

        model_name = get_best_available_model()
        model = genai.GenerativeModel(model_name)
        
        response = model.generate_content(
            content_parts,
            generation_config={"response_mime_type": "application/json"}
        )

        raw_text = response.text.strip()
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        questions = json.loads(raw_text)
        if isinstance(questions, list) and len(questions) > 0:
            return questions, None
        return None, "AI से सवालों की सूची खाली आई।"

    except Exception as e:
        logger.error(f"AI Generation Error: {e}\n{traceback.format_exc()}")
        return None, str(e)
    finally:
        if temp_uploaded_file:
            try:
                genai.delete_file(temp_uploaded_file.name)
            except Exception:
                pass
        if temp_local_path and os.path.exists(temp_local_path):
            try:
                os.remove(temp_local_path)
            except Exception:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_SESSIONS[user_id] = {"busy": False}
    
    msg = (
        "🧠 **AI Quiz Revision Bot Ready!**\n\n"
        "💡 **अब हर बार बिल्कुल नए सवाल मिलेंगे:**\n"
        "• आप अपनी **रंगीन PDF या नोट्स की फ़ोटो** सीधे इस चैट में भेज सकते हैं।\n"
        "• या नीचे दिए गए किसी भी बटन पर क्लिक करें।\n\n"
        "मोड चुनें और शुरू करें:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_document_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """सीधे चैट में भेजी गई PDF या फोटो को सेव करना"""
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("📥 **मटीरियल लोड हो रहा है, कृपया 3 सेकंड प्रतीक्षा करें...**")

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
            await status_msg.edit_text("✅ **नोट्स की फ़ोटो लोड हो गई!**\nअब मोड चुनें और क्विज़ खेलें:", reply_markup=get_main_keyboard())

    except Exception as e:
        await status_msg.edit_text(f"❌ लोड करने में समस्या: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "sync_drive_pdf":
        msg = await query.message.reply_text("🔄 **Drive से PDF लोड हो रही है...**")
        success, err = await sync_drive_file(user_id)
        if success:
            return await msg.edit_text("✅ **Drive PDF लोड हो गई!** अब मोड चुनें:", reply_markup=get_main_keyboard())
        else:
            return await msg.edit_text(f"❌ Drive सिंक विफल:\n`{err}`")

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
        return await context.bot.send_message(chat_id, "❌ `GEMINI_API_KEY` सेट नहीं है! कृपया Render में Key जोड़ें।")

    if user_id not in USER_STUDY_MATERIAL:
        if DRIVE_FILE_ID:
            temp_sync = await context.bot.send_message(chat_id, "📥 **Drive से PDF लोड हो रही है...**")
            success, err = await sync_drive_file(user_id)
            await temp_sync.delete()
            if not success:
                return await context.bot.send_message(chat_id, f"❌ Drive से PDF लोड नहीं हो पाई:\n`{err}`\n\n💡 **सलाह:** आप अपनी रंगीन PDF सीधे इस चैट में भी भेज सकते हैं!")
        else:
            return await context.bot.send_message(chat_id, "⚠️ कोई नोट्स नहीं मिले! कृपया अपनी रंगीन PDF या फोटो इस चैट में भेजें।")

    load_msg = await context.bot.send_message(chat_id, "🤖 **AI आपके नोट्स पढ़कर 5 बिल्कुल नए सवाल तैयार कर रहा है... (5-10 सेकंड)**")

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
        res = f"🎉 **क्विज़ समाप्त!**\n\n✅ सही उत्तर: {score}/{total}\n📊 आपका स्कोर: {per}%\n\nअगले 5 बिल्कुल नए सवाल खेलने के लिए नीचे से मोड चुनें:"
        await context.bot.send_message(chat_id, res, parse_mode="Markdown", reply_markup=get_main_keyboard())
        user_data["busy"] = False
        return

    q = quiz[idx]
    
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

        poll_options = poll_options[:4]
        correct_id = parse_correct_answer(q.get('answer'), poll_options)

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

    except Exception as e:
        logger.error(f"Poll Send Failed on Q{idx+1}: {e}")
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
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))
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
