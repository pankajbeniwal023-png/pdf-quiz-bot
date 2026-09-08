import os
import json
import random
import asyncio
import logging
import re
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# AI Client Setup
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ⚠️ FIXED: Updated exact model name as required by Google API
MODEL_NAME = "gemini-3.6-flash"

USER_DATA = {}

# --- Render Port Binding ---
async def handle_root(request):
    return web.Response(text="Revision Bot Active")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- File Handling ---
async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        # JSON Format Fix
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list):
                    data = data[key]
                    break

        USER_DATA[user_id] = data

        keyboard = [
            [InlineKeyboardButton("🔄 कठिन भाषा (Twisted)", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 उल्टा क्विज़ (Reverse)", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 कथन/कारण (Statement)", callback_data="mode_statement")]
        ]
        await update.message.reply_text(
            f"✅ {len(data)} सवाल लोड हुए!\n\nAI अब हर बार नए शब्दों का प्रयोग करेगा।", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await update.message.reply_text("❌ JSON फाइल का फॉर्मेट सही नहीं है।")

# --- AI Generation Logic ---
async def generate_ai_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    orig_q = item.get('question', '')
    options = item.get('options', [])
    correct_idx = item.get('answer', 0)
    
    correct_val = options[correct_idx] if isinstance(correct_idx, int) and correct_idx < len(options) else options[0]

    prompts = {
        "mode_twisted": f"इस प्रश्न की भाषा को बिल्कुल नए और कठिन हिंदी शब्दों में बदलो। प्रश्न का अर्थ वही रहे पर शब्द एकदम अलग हों। प्रश्न: '{orig_q}'",
        "mode_reverse": f"उत्तर '{correct_val}' है। इस शब्द के लिए एक बहुत ही कठिन पहेलीनुमा विवरण लिखो जो '{orig_q}' पर आधारित हो।",
        "mode_statement": f"कथन: '{orig_q}'। इसके लिए एक नया 'कारण' (Reason) लिखो जो सही जवाब '{correct_val}' को सिद्ध करे।"
    }

    prompt = f"""
तुम एक परीक्षा विशेषज्ञ हो। केवल इस डेटा का उपयोग करो: सवाल='{orig_q}', जवाब='{correct_val}'।
कार्य: {prompts[mode]}
नियम:
1. भाषा: उच्च स्तरीय देवनागरी हिंदी। 
2. हर बार नए पर्यायवाची शब्दों का प्रयोग करें।
3. आउटपुट केवल शुद्ध JSON: {{"q": "सवाल", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": index_number}}
"""

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9,
            )
        )
        res_text = response.text.strip()
        if "```json" in res_text:
            match = re.search(r'```json\s*(.*?)\s*```', res_text, re.DOTALL)
            if match:
                res_text = match.group(1)
        return json.loads(res_text)
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        return None

# --- Button Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ फाइल दोबारा भेजें।")

    await query.edit_message_text(text="⚡ AI भाषा बदल रहा है...")

    q_data = await generate_ai_question(user_id, mode)

    if q_data and 'q' in q_data and 'o' in q_data and 'a' in q_data:
        try:
            await context.bot.send_poll(
                chat_id=query.message.chat_id,
                question=str(q_data['q'])[:300],
                options=[str(o)[:100] for o in q_data['o']],
                correct_option_id=int(q_data['a']),
                type=Poll.QUIZ,
                is_anonymous=False
            )
            keyboard = [[InlineKeyboardButton("अगला नया सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "तैयार?", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Poll Send Error: {e}")
            await context.bot.send_message(query.message.chat_id, "⚠️ पोल भेजने में गड़बड़ हुई।")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ AI एरर। कृपया दोबारा बटन दबाएँ।")

# --- Main ---
async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("फाइल भेजें!")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_click))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
