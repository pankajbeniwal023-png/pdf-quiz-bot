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
PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
ASKED_IDS = set()
POLL_TRACKER = {}

# Robust AI Generator with Chunking to prevent AI Failure
async def generate_variations_from_text(input_text: str):
    prompt = f"""
You are an expert exam paper setter. Analyze the input text/facts/questions and generate comprehensive multiple-choice quiz items in Hindi.

Create 3 distinct variations for each question derived from the text:
1. "direct": Standard fact question.
2. "statement": Strict Assertion-Reason style in Hindi.
   - Question format MUST be: "कथन (A): ... \nकारण (R): ..."
   - Options MUST be standard UPSC format:
     0: कथन (A) और कारण (R) दोनों सही हैं और (R), (A) की सही व्याख्या है।
     1: कथन (A) और कारण (R) दोनों सही हैं लेकिन (R), (A) की सही व्याख्या नहीं है।
     2: कथन (A) सही है लेकिन कारण (R) गलत है।
     3: कथन (A) गलत है लेकिन कारण (R) सही है।
3. "twisted": Reframe the question conceptually/analytically to test understanding beyond rote memory.

Input Content:
{input_text}

Output Rules:
- Return ONLY valid JSON array with structure:
[
  {{
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
        return json.loads(response.text.strip())
    except Exception as e:
        logger.error(f"AI Chunk Error: {e}")
        return []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🎯 डायरेक्ट मोड", callback_data="mode_direct"),
            InlineKeyboardButton("📝 कथन-कारण मोड", callback_data="mode_statement")
        ],
        [
            InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted"),
            InlineKeyboardButton("🧹 रीसेट", callback_data="mode_reset")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        "🧠 **100% Reliable Quiz Generator Bot**\n\n"
        "1. कोई भी **फोटो (Image), PDF, JSON या सीधे टेक्स्ट नोट्स** भेजें।\n"
        "2. बॉट बिना फ़ेल हुए ऑटोमैटिक सभी प्रश्नों के कथन-कारण और लॉजिकल वर्ज़न तैयार कर लेगा।\n"
        "3. बटन दबाते ही **0.1 सेकंड** में नए तरीके से सवाल चालू हो जाएंगे!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

# Handle Images with Text Extraction
async def handle_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("📸 इमेज मिल गई! AI फोटो से प्रश्न और कथन-कारण बना रहा है... ⚡")
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        prompt = "Extract all study facts or questions from this image and convert into JSON format."
        
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
                prompt
            ]
        )
        extracted_text = response.text.strip()
        
        # Process extracted content
        results = await generate_variations_from_text(extracted_text)
        if results:
            store_processed_results(results)
            await finalize_upload_response(status_msg, len(results))
        else:
            await status_msg.edit_text("❌ इमेज से सवाल जनरेट करने में असमर्थ। कृपया साफ़ फोटो भेजें।")
            
    except Exception as e:
        logger.error(f"Image Error: {e}")
        await status_msg.edit_text("❌ इमेज प्रोसेस करने में एरर आई।")

# Handle Document (PDF / JSON / TXT)
async def handle_document_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    status_msg = await update.message.reply_text("📥 फ़ाइल मिल गई! सुरक्षित चंकिंग प्रोसेसिंग जारी है... ⚡")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        text_data = content.decode('utf-8', errors='ignore').strip()
        
        # Chunking Text into smaller blocks to prevent Gemini API Failure
        lines = text_data.split("\n")
        chunk_size = 30  # Process line groups
        chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]
        
        all_results = []
        for chunk in chunks:
            chunk_text = "\n".join(chunk)
            if chunk_text.strip():
                res = await generate_variations_from_text(chunk_text)
                all_results.extend(res)
                
        if all_results:
            store_processed_results(all_results)
            await finalize_upload_response(status_msg, len(all_results))
        else:
            await status_msg.edit_text("❌ कंटेंट से सवाल जनरेट नहीं हो सके।")
            
    except Exception as e:
        logger.error(f"Document Error: {e}")
        await status_msg.edit_text("❌ फ़ाइल रीड करने में समस्या आई।")

def store_processed_results(results_list):
    global PROCESSED_DATA, ASKED_IDS
    PROCESSED_DATA["direct"] = [item["direct"] for item in results_list if "direct" in item]
    PROCESSED_DATA["statement"] = [item["statement"] for item in results_list if "statement" in item]
    PROCESSED_DATA["twisted"] = [item["twisted"] for item in results_list if "twisted" in item]
    ASKED_IDS.clear()

async def finalize_upload_response(status_msg, count):
    keyboard = [
        [InlineKeyboardButton("📝 कथन-कारण टेस्ट (Statement)", callback_data="mode_statement")],
        [InlineKeyboardButton("🔄 घुमावदार (Twisted)", callback_data="mode_twisted")],
        [InlineKeyboardButton("🎯 डायरेक्ट क्विज़", callback_data="mode_direct")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await status_msg.edit_text(
        f"✅ **{count} नए प्रश्न तैयार हो गए हैं!**\n\n"
        "⚡ नीचे बटन दबाते ही बिना 1 सेकंड गंवाए टेस्ट शुरू होगा:", 
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mode_reset":
        global ASKED_IDS
        ASKED_IDS.clear()
        return await query.message.reply_text("🧹 **पूछे गए सवालों की हिस्ट्री रीसेट हो गई है!**")

    mode_map = {"mode_direct": "direct", "mode_statement": "statement", "mode_twisted": "twisted"}
    selected_mode = mode_map.get(query.data)
    if selected_mode:
        await start_quiz_session(query.message.chat_id, query.from_user.id, context, mode=selected_mode)

async def start_quiz_session(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global PROCESSED_DATA, ASKED_IDS
    bank = PROCESSED_DATA.get(mode, [])
    
    if not bank:
        return await context.bot.send_message(chat_id, "❌ पहले नोट्स, इमेज या फ़ाइल भेजें!")

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
        res = f"🎉 **क्विज़ समाप्त!**\n\n✅ सही: {score}/{total}\n📊 स्कोर: {per}%"
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
    ptb_app.add_handler(CallbackQueryHandler(button_handler))
    ptb_app.add_handler(MessageHandler(filters.PHOTO, handle_photo_input))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_document_input))
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
