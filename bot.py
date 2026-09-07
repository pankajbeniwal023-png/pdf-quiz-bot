import os
import json
import logging
import asyncio
import random
import warnings
import fitz  # PyMuPDF
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
from google import genai
from google.genai import types

warnings.filterwarnings("ignore")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_URL = os.environ.get("RENDER_URL")

ai_client = genai.Client(api_key=GEMINI_API_KEY)
USER_PDF_DATA = {}
POLL_TRACKER = {}

# PDF से टेक्स्ट निकालने का फ़ंक्शन
def extract_text_from_pdf(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    return full_text

# टेक्स्ट से सवाल बनाने का फ़ंक्शन
async def generate_questions_from_text(pdf_text, num_questions):
    # टेक्स्ट का रैंडम हिस्सा चुनना ताकि हर बार अलग सवाल बनें
    max_chars = 30000
    if len(pdf_text) > max_chars:
        start_idx = random.randint(0, len(pdf_text) - max_chars)
        selected_text = pdf_text[start_idx : start_idx + max_chars]
    else:
        selected_text = pdf_text

    random_seed = random.randint(1000, 99999)
    prompt = f"""
तुम एक परीक्षा विशेषज्ञ हो। नीचे दिए गए अध्ययन टेक्स्ट को ध्यान से पढ़ो और ठीक {num_questions} बहुविकल्पीय प्रश्न (MCQs) शुद्ध एवं सरल हिंदी भाषा में बनाओ।

**नियम:**
1. (Seed: {random_seed}) हर बार नए, अलग और महत्वपूर्ण फैक्ट्स से सवाल चुनो।
2. प्रत्येक प्रश्न के 4 स्पष्ट विकल्प हों।
3. केवल शुद्ध JSON Array आउटपुट दो।

JSON प्रारूप:
[
  {{
    "question": "प्रश्न?",
    "options": ["ऑप्शन 1", "ऑप्शन 2", "ऑप्शन 3", "ऑप्शन 4"],
    "answer": 0
  }}
]

टेक्स्ट:
{selected_text}
"""

    models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash']
    for model_name in models_to_try:
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.9,
                ),
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text.replace("```", "").strip()

            data = json.loads(raw_text)
            if isinstance(data, list) and len(data) > 0:
                return data
        except Exception as e:
            logger.warning(f"Model {model_name} Error: {e}")
            await asyncio.sleep(1)

    return []

# --- टेलीग्राम हैंडलर्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in USER_PDF_DATA:
        del USER_PDF_DATA[user_id]
    await update.message.reply_text("👋 **PDF Revision Bot**\nअपनी PDF फ़ाइल यहाँ भेजें।")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith('.pdf'):
        return await update.message.reply_text("❌ केवल PDF भेजें।")

    msg = await update.message.reply_text("📥 PDF पढ़ी जा रही है...")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        
        # PDF से तुरंत टेक्स्ट निकालें
        pdf_text = extract_text_from_pdf(bytes(pdf_bytes))

        if not pdf_text.strip():
            return await msg.edit_text("❌ इस PDF में टेक्स्ट नहीं मिला (हो सकता है यह केवल स्कैन की गई इमेजेस हों)।")

        user_id = update.effective_user.id
        USER_PDF_DATA[user_id] = {
            "text": pdf_text,
            "filename": doc.file_name
        }

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 10 Questions", callback_data="gen_10"), InlineKeyboardButton("🔥 20 Questions", callback_data="gen_20")],
            [InlineKeyboardButton("⚡ 30 Questions", callback_data="gen_30"), InlineKeyboardButton("🚀 50 Questions", callback_data="gen_50")]
        ])

        await msg.edit_text(f"✅ **PDF लोड हो गई!**\n📄 `{doc.file_name}`\n\nकितने सवालों से रिवीजन करना है?", reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error: {e}")
        await msg.edit_text("❌ PDF प्रोसेस करने में समस्या आई।")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("gen_"):
        num_qs = int(data.split("_")[1])
        
        if user_id not in USER_PDF_DATA or "text" not in USER_PDF_DATA[user_id]:
            return await query.edit_message_text("❌ PDF डेटा नहीं मिला। फिर से PDF भेजें: /start")

        pdf_text = USER_PDF_DATA[user_id]["text"]
        await query.edit_message_text(f"⚡ PDF से {num_qs} नए सवाल तैयार हो रहे हैं... ⏳")

        quiz_data = await generate_questions_from_text(pdf_text, num_qs)

        if not quiz_data:
            return await context.bot.send_message(chat_id, "❌ सर्वर रिस्पॉन्स नहीं दे पाया। कृपया बटन दोबारा दबाएँ।")

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
        res = f"🎉 **रिवीजन क्विज़ समाप्त!**\n\n✅ सही उत्तर: {score} / {total}\n📊 आपका स्कोर: {per}%"
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

async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    ptb_app.add_handler(CallbackQueryHandler(handle_callback))
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
