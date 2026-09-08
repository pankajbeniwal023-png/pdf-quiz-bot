import os, json, random, asyncio, logging, re
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {}

# --- Render Alive Server ---
async def handle_root(request): return web.Response(text="Revision Bot Active")
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# --- File Handling ---
async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        raw_data = json.loads(content.decode('utf-8'))

        # JSON format handle
        if isinstance(raw_data, dict):
            for key in raw_data:
                if isinstance(raw_data[key], list):
                    USER_DATA[user_id] = raw_data[key]
                    break
        elif isinstance(raw_data, list):
            USER_DATA[user_id] = raw_data
        
        keyboard = [
            [InlineKeyboardButton("🔄 भाषा बदलो (Twisted)", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 उल्टा क्विज़ (Reverse)", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 कथन/कारण (Statement)", callback_data="mode_statement")]
        ]
        await update.message.reply_text(f"✅ {len(USER_DATA[user_id])} सवाल लोड हुए!", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"❌ फाइल एरर: {e}")

# --- AI Generator (Sudhara Hua) ---
async def generate_ai_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    original_q = item.get('question', '')
    options = item.get('options', [])
    correct_opt = options[item.get('answer', 0)]

    # सख्त निर्देश: ओरिजिनल शब्द इस्तेमाल मत करो
    prompts = {
        "mode_twisted": f"इस प्रश्न की भाषा को 100% बदल दो। नए कठिन हिंदी शब्दों का प्रयोग करो लेकिन अर्थ वही रहे। प्रश्न: '{original_q}'",
        "mode_reverse": f"रिवर्स क्विज़: मुख्य शब्द '{correct_opt}' है। इसके लिए एक कठिन विश्लेषणात्मक विवरण लिखो जो '{original_q}' पर आधारित हो।",
        "mode_statement": f"कथन (A): '{original_q}'। इस पर आधारित एक तर्कसंगत कारण (R) लिखो। उत्तर '{correct_opt}' के इर्द-गिर्द हो।"
    }

    instruction = f"""
    तुम एक UPSC स्तर के पेपर सेटर हो।
    कार्य: {prompts[mode]}
    नियम:
    1. मूल प्रश्न के वाक्यों को कॉपी न करें, उन्हें पूरी तरह 'Rephrase' करें।
    2. केवल शुद्ध देवनागरी हिंदी।
    3. आउटपुट केवल JSON हो: {{"q": "नया सवाल", "o": ["V1", "V2", "V3", "V4"], "a": index}}
    """
    
    try:
        # मॉडल को gemini-1.5-flash पर रखा है (ज़्यादा स्टेबल है)
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-1.5-flash',
            contents=instruction,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9 # वैरायटी के लिए
            )
        )
        
        # JSON साफ़ करना (अगर AI फालतू टेक्स्ट दे)
        resp_text = response.text.strip()
        # Markdown हटाना अगर मौजूद हो
        if "```json" in resp_text:
            resp_text = re.search(r'```json\s*(.*?)\s*```', resp_text, re.DOTALL).group(1)
        
        return json.loads(resp_text)
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return None # अब यह ओरिजिनल सवाल नहीं भेजेगा

# --- Button Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    await query.edit_message_text(text="⚡ AI सवाल को घुमा रहा है... (New Language Generating)")
    
    q_data = await generate_ai_question(user_id, mode)
    
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
            keyboard = [[InlineKeyboardButton("अगला नया सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "अगले सवाल के लिए दबाएँ:", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, f"⚠️ पोल एरर: {e}")
    else:
        # अब आपको पता चलेगा कि AI फेल हुआ है
        await context.bot.send_message(query.message.chat_id, "❌ AI भाषा बदलने में फेल हो गया। कृपया दोबारा दबाएँ।")

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
