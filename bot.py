import os
import io
import json
import random
import logging
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
    ContextTypes
)
import pypdf
from google import genai
from google.genai import types

# --- लॉगिंग सेटअप ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- कॉन्फ़िगरेशन ---
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_URL = os.environ.get("RENDER_URL")

# Gemini AI क्लाइंट
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# रैम/मेमोरी स्टोर (GitHub की ज़रूरत नहीं है)
USER_PDF_DATA = {}
POLL_TRACKER = {}

# --- Helper: PDF से टेक्स्ट निकालना ---
def extract_text_from_pdf(pdf_bytes):
    pdf_file = io.BytesIO(pdf_bytes)
    reader = pypdf.PdfReader(pdf_file)
    extracted_text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            extracted_text += t + "\n"
    return extracted_text.strip()

# --- Helper: Gemini AI से सवाल बनवाना ---
async def generate_quiz_from_text(pdf_text: str, num_questions: int):
    prompt = f"""
तुम एक बहुत ही सख्त प्रतियोगी परीक्षा परीक्षा विशेषज्ञ हो।
नीचे दिए गए टेक्स्ट को ध्यान से पढ़ो और ठीक {num_questions} बहुविकल्पीय प्रश्न (MCQs) हिंदी में तैयार करो।

**सख्त नियम:**
1. उत्तर केवल और केवल नीचे दिए गए टेक्स्ट में मौजूद तथ्यों पर आधारित होने चाहिए। अपने मन या बाहर के ज्ञान से कोई उत्तर मत देना।
2. प्रश्नों का तरीका और भाषा हर बार अलग और नई होनी चाहिए ताकि छात्र का बेहतरीन रिवीजन हो सके।
3. प्रत्येक प्रश्न के ठीक 4 विकल्प होने चाहिए।
4. JSON संरचना बिल्कुल इस प्रारूप में होनी चाहिए (बिना किसी अतिरिक्त Markdown मान के):

[
  {{
    "question": "प्रश्न का पाठ",
    "options": ["विकल्प 1", "विकल्प 2", "विकल्प 3", "विकल्प 4"],
    "answer": 0
  }}
]

ध्यान दें: "answer" इंडेक्स 0 से 3 तक होना चाहिए जो 'options' सूची में सही विकल्प का इंडेक्स दर्शाता है।

टेक्स्ट सामग्री:
{pdf_text[:12000]}  
"""
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        data = json.loads(response.text)
        return data
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None

# --- बॉट कमांड्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in USER_PDF_DATA:
        del USER_PDF_DATA[user_id]
    
    welcome_msg = (
        "👋 **PDF Quiz Generator Bot में आपका स्वागत है!**\n\n"
        "📖 **इस्तेमाल कैसे करें:**\n"
        "1. अपनी कोई भी **PDF फाइल** यहाँ भेजें।\n"
        "2. नीचे दिए गए बटन से चुनें कि आपको कितने सवाल (10, 20, 30) हल करने हैं।\n"
        "3. बॉट आपकी PDF से ताज़ा सवाल बनाकर टेस्ट शुरू कर देगा!\n\n"
        "⚠️ *नोट: कोई भी सवाल डेटाबेस में सेव नहीं होता, हर बार बिल्कुल नए सवाल बनेंगे!*"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith('.pdf'):
        return await update.message.reply_text("❌ कृपया केवल PDF फ़ाइल ही भेजें।")

    msg = await update.message.reply_text("📥 PDF डाउनलोड हो रही है और टेक्स्ट निकाला जा रहा है...")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        pdf_text = extract_text_from_pdf(pdf_bytes)

        if not pdf_text or len(pdf_text) < 50:
            return await msg.edit_text("❌ इस PDF से टेक्स्ट नहीं पढ़ा जा सका। (हो सकता है यह केवल स्कैन की गई इमेज हो)।")

        user_id = update.effective_user.id
        USER_PDF_DATA[user_id] = {
            "text": pdf_text,
            "filename": doc.file_name
        }

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 10 Questions", callback_data="gen_10")],
            [InlineKeyboardButton("🔥 20 Questions", callback_data="gen_20")],
            [InlineKeyboardButton("⚡ 30 Questions", callback_data="gen_30")]
        ])

        await msg.edit_text(
            f"✅ **PDF सफलतापूर्वक लोड हो गई!**\n📄 फ़ाइल: `{doc.file_name}`\n\n"
            "👇 **कितने सवालों का क्विज़ खेलना चाहते हैं? बटन पर क्लिक करें:**",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"PDF handling error: {e}")
        await msg.edit_text("❌ PDF प्रोसेस करने में कोई त्रुटि हुई।")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("gen_"):
        num_qs = int(data.split("_")[1])
        
        if user_id not in USER_PDF_DATA or "text" not in USER_PDF_DATA[user_id]:
            return await query.edit_message_text("❌ PDF का डेटा नहीं मिला। कृपया फिर से PDF अपलोड करें: /start")

        pdf_text = USER_PDF_DATA[user_id]["text"]
        await query.edit_message_text(f"🤖 AI आपकी PDF से {num_qs} नए सवाल तैयार कर रहा है... कृपया 5-10 सेकंड इंतज़ार करें ⏳")

        quiz_data = await generate_quiz_from_text(pdf_text, num_qs)

        if not quiz_data:
            return await context.bot.send_message(chat_id, "❌ सवाल बनाने में कोई समस्या आई। कृपया बटन पर फिर से क्लिक करें।")

        context.user_data.clear()
        context.user_data.update({
            "quiz": quiz_data,
            "idx": 0,
            "score": 0,
            "total": len(quiz_data),
            "busy": True
        })

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
        res = (
            f"🎉 **क्विज़ समाप्त!**\n\n"
            f"✅ सही उत्तर: {score} / {total}\n"
            f"📊 आपका स्कोर: {per}%\n\n"
            f"💡 नया टेस्ट खेलने के लिए नई PDF भेजें या फिर से /start करें।"
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

# --- WEBHOOK & MAIN ---
async def handle_webhook(request):
    app = request.app['bot_app']
    if request.method == 'POST':
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        return web.Response(text="OK")
    return web.Response(text="Bot Alive")

def main():
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    # Webhook सेटअप (Render के लिए)
    port = int(os.environ.get("PORT", 10000))
    
    async def on_startup(application):
        await application.bot.set_webhook(url=f"{RENDER_URL}/{TOKEN}", drop_pending_updates=True)

    app.post_init = on_startup
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
