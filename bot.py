import os, json, random, asyncio, logging, re
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai  # Latest SDK
from google.genai import types

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# AI Client Setup
client = genai.Client(api_key=GEMINI_API_KEY)

# आपके स्क्रीनशॉट के अनुसार मॉडल का नाम
# नोट: अगर ये 404 एरर दे, तो इसे "gemini-1.5-flash" कर दें
MODEL_NAME = "gemini-3.6-flash" 

USER_DATA = {}

# --- Web Server for Deployment ---
async def handle_root(request): return web.Response(text="Bot is running...")
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# --- JSON File Handler ---
async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        # JSON extraction logic
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list):
                    data = data[key]
                    break
        
        USER_DATA[user_id] = data
        keyboard = [
            [InlineKeyboardButton("🔄 Twisted Mode", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 Reverse Mode", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 Statement Mode", callback_data="mode_statement")]
        ]
        await update.message.reply_text(f"✅ {len(data)} सवाल लोड हुए!", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"❌ फाइल एरर: {str(e)}")

# --- AI Generation ---
async def generate_ai_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    orig_q = item.get('question', '')
    options = item.get('options', [])
    correct_val = options[item.get('answer', 0)]

    prompts = {
        "mode_twisted": f"इस प्रश्न को कठिन हिंदी में बदलें: '{orig_q}'",
        "mode_reverse": f"जवाब '{correct_val}' है। इसके लिए '{orig_q}' पर आधारित पहेली बनाएं।",
        "mode_statement": f"कथन: '{orig_q}'। इसका सही 'कारण' लिखें जो '{correct_val}' को सिद्ध करे।"
    }

    sys_prompt = f"""
    तुम एक एक्सपर्ट टीचर हो। केवल JSON में जवाब दो।
    Data: Question: {orig_q}, Answer: {correct_val}
    Task: {prompts[mode]}
    Format: {{"q": "सवाल", "o": ["विकल्प1", "2", "3", "4"], "a": index}}
    """
    
    try:
        # New SDK Syntax
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=sys_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.8
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return None

# --- Interaction Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ कृपया JSON फाइल दोबारा भेजें।")

    msg = await query.message.reply_text("⚡ AI प्रोसेसिंग...")
    q_data = await generate_ai_question(user_id, mode)
    await msg.delete()

    if q_data:
        try:
            await context.bot.send_poll(
                chat_id=query.message.chat_id,
                question=q_data['q'][:300],
                options=[str(o)[:100] for o in q_data['o']],
                correct_option_id=int(q_data['a']),
                type=Poll.QUIZ,
                is_anonymous=False
            )
            btn = [[InlineKeyboardButton("अगला सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "तैयार?", reply_markup=InlineKeyboardMarkup(btn))
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, f"⚠️ एरर: {e}")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ AI ने जवाब नहीं दिया। मॉडल का नाम चेक करें।")

async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("JSON फाइल भेजें!")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_click))
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
