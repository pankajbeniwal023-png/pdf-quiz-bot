import os
import json
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

# मेमोरी स्टोर
USER_PDF_DATA = {}
POLL_TRACKER = {}

# --- Gemini API Test Function ---
async def test_gemini_api():
    try:
        prompt = "एक आसान सामान्य ज्ञान प्रश्न JSON प्रारूप में बनाओ। प्रारूप: [{\"question\": \"...\", \"options\": [\"a\", \"b\", \"c\", \"d\"], \"answer\": 0}]"
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        return True, response.text
    except Exception as e:
        return False, str(e)

# --- Helper: Gemini AI से सवाल बनवाना (PDF/Text) ---
async def generate_quiz_from_pdf_bytes(pdf_bytes: bytes, file_name: str, num_questions: int):
    prompt = f"""
तुम एक बहुत ही सख्त प्रतियोगी परीक्षा विशेषज्ञ हो।
इस अपलोड की गई PDF/इमेज फाइल को ध्यान से पढ़ो और ठीक {num_questions} बहुविकल्पीय प्रश्न (MCQs) हिंदी में तैयार करो।

**सख्त नियम:**
1. उत्तर केवल और केवल इस फ़ाइल/इमेज में मौजूद सामग्री पर आधारित होने चाहिए।
2. प्रत्येक प्रश्न के ठीक 4 विकल्प होने चाहिए।
3. आउटपुट केवल और केवल शुद्ध JSON एरे (Array) में होना चाहिए।

JSON प्रारूप:
[
  {{
    "question": "प्रश्न का पाठ",
    "options": ["विकल्प 1", "विकल्प 2", "विकल्प 3", "विकल्प 4"],
    "answer": 0
  }}
]

ध्यान दें: "answer" का मान 0 से 3 तक का इंडेक्स होना चाहिए।
"""
    try:
        pdf_part = types.Part.from_bytes(
            data=pdf_bytes,
            mime_type="application/pdf",
        )

        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=[pdf_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        data = json.loads(response.text)
        return data
    except Exception as e:
        logger.error(f"Gemini API error: {e}", exc_info=True)
        return None

# --- बॉट कमांड्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in USER_PDF_DATA:
        del USER_PDF_DATA[user_id]
    
    welcome_msg = (
        "👋 **PDF Quiz Generator Bot में आपका स्वागत है!**\n\n"
        "📖 **इस्तेमाल कैसे करें:**\n"
        "1. अपनी कोई भी **PDF फ़ाइल** यहाँ भेजें।\n"
        "2. फिर प्रश्नों की संख्या चुनें।\n\n"
        "🔍 **Gemini API जाँचने के लिए:** /testgemini भेजें।"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🧪 Gemini API की जाँच की जा रही है...")
    success, result = await test_gemini_api()
    if success:
        await msg.edit_text(f"✅ **Gemini API काम कर रही है!**\n\n**AI का रिस्पॉन्स:**\n`{result}`", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ **Gemini API में एरर है:**\n`{result}`", parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith('.pdf'):
        return await update.message.reply_text("❌ कृपया केवल PDF फ़ाइल ही भेजें।")

    msg = await update.message.reply_text("📥 PDF डाउनलोड हो रही है...")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()

        user_id = update.effective_user.id
        USER_PDF_DATA[user_id] = {
            "bytes": bytes(pdf_bytes),
            "filename": doc.file_name
        }

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 10 Questions", callback_data="gen_10")],
            [InlineKeyboardButton("🔥 20 Questions", callback_data="gen_20")],
            [InlineKeyboardButton("⚡ 30 Questions", callback_data="gen_30")]
        ])

        await msg.edit_text(
            f"✅ **PDF सफलतापूर्वक लोड हो गई!**\n📄 फ़ाइल: `{doc.file_name}`\n\n"
            "👇 **कितने सवालों का क्विज़ खेलना चाहते हैं?**",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"PDF Handling Error: {e}", exc_info=True)
        await msg.edit_text("❌ PDF लोड करने में त्रुटि हुई। कृपया फिर से प्रयास करें।")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("gen_"):
        num_qs = int(data.split("_")[1])
        
        if user_id not in USER_PDF_DATA or "bytes" not in USER_PDF_DATA[user_id]:
            return await query.edit_message_text("❌ PDF का डेटा नहीं मिला। कृपया फिर से PDF भेजें: /start")

        pdf_bytes = USER_PDF_DATA[user_id]["bytes"]
        file_name = USER_PDF_DATA[user_id]["filename"]
        
        await query.edit_message_text(f"🤖 Gemini AI आपकी PDF को पढ़ रहा है और {num_qs} सवाल बना रहा है... ⏳")

        quiz_data = await generate_quiz_from_pdf_bytes(pdf_bytes, file_name, num_qs)

        if not quiz_data:
            return await context.bot.send_message(chat_id, "❌ सवाल बनाने में समस्या आई। कृपया `/testgemini` चलाकर देखें या फिर से प्रयास करें।")

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
        res = f"🎉 **क्विज़ समाप्त!**\n\n✅ सही उत्तर: {score} / {total}\n📊 आपका स्कोर: {per}%"
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

# --- AIOHTTP WEB SERVER & WEBHOOK ---
async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("testgemini", test_cmd))
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
            logger.error(f"Error handling update: {e}")
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
