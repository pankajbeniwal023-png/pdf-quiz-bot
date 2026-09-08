import os, json, random, asyncio, logging
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Environment Variables
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {}

# --- वेब सर्वर Render के लिए ---
async def handle_root(request):
    return web.Response(text="Bot is Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render PORT पर्यावरण चर (env var) प्रदान करता है
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# --- बोट फंक्शन्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 अल्ट्रा रिविजन बोट तैयार है! अपनी JSON फाइल भेजें।")

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        content = await file.download_as_bytearray()
        USER_DATA[user_id] = json.loads(content.decode('utf-8'))
        
        keyboard = [
            [InlineKeyboardButton("🎯 MCQ", callback_data="mode_mcq")],
            [InlineKeyboardButton("🔄 Reverse Quiz", callback_data="mode_reverse")]
        ]
        await update.message.reply_text(f"✅ {len(USER_DATA[user_id])} सवाल लोड हुए!", reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        await update.message.reply_text("❌ फाइल में गड़बड़ है।")

async def generate_smart_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    original_q = item['question']
    correct_opt = item['options'][item['answer']]
    
    prompt = f"सवाल: '{original_q}', जवाब: '{correct_opt}'। इसे {mode} में बदलें (JSON format: {{'q': '...', 'o': [...], 'a': index}})"
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text.strip())
    except: return None

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    q_data = await generate_smart_question(update.effective_user.id, query.data)
    if q_data:
        await context.bot.send_poll(
            chat_id=query.message.chat_id,
            question=q_data['q'][:300],
            options=q_data['o'],
            correct_option_id=q_data['a'],
            type=Poll.QUIZ, is_anonymous=False
        )

# --- मुख्य फंक्शन (Main) ---
async def main():
    # वेब सर्वर शुरू करें
    await start_web_server()
    
    # बोट शुरू करें
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # पोलिंग शुरू करें
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # बोट को चालू रखने के लिए अनंत लूप
        await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
