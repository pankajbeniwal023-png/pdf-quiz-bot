import os, json, random, asyncio, logging, re
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

# AI Client Initialization
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {}

# --- Render Port Binding (ताकि Building पर न अटके) ---
async def handle_root(request): return web.Response(text="Bot is running perfectly!")
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

        # आपके JSON फॉर्मेट को सही से पकड़ना
        if isinstance(raw_data, dict):
            for key in raw_data:
                if isinstance(raw_data[key], list):
                    USER_DATA[user_id] = raw_data[key]
                    break
        elif isinstance(raw_data, list):
            USER_DATA[user_id] = raw_data
        
        keyboard = [
            [InlineKeyboardButton("🔄 कठिन भाषा (Twisted)", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 उल्टा क्विज़ (Reverse)", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 कथन/कारण (Statement)", callback_data="mode_statement")]
        ]
        await update.message.reply_text(f"✅ {len(USER_DATA[user_id])} सवाल मिले!\nअब रिविजन मोड चुनें:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text("❌ JSON फाइल का फॉर्मेट सही नहीं है।")

# --- AI Generator Engine ---
async def generate_ai_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    orig_q = item.get('question', '')
    options = item.get('options', [])
    correct_val = options[item.get('answer', 0)]

    # AI के लिए प्रॉम्प्ट - एकदम सख्त भाषा में
    prompts = {
        "mode_twisted": f"इस प्रश्न की भाषा को 100% कठिन और नए हिंदी शब्दों में बदलो। ओरिजिनल सवाल के शब्दों का उपयोग न करें। प्रश्न: '{orig_q}'",
        "mode_reverse": f"यह उल्टा क्विज़ है। मुख्य उत्तर है '{correct_val}'। इस शब्द के लिए एक बहुत ही कठिन और घुमावदार विवरण तैयार करो जो '{orig_q}' तथ्य पर आधारित हो।",
        "mode_statement": f"कथन (Assertion): '{orig_q}'। इस पर आधारित एक तार्किक 'कारण' (Reason) लिखो। सही जवाब '{correct_val}' के इर्द-गिर्द हो।"
    }

    instruction = f"""
    तुम एक वरिष्ठ परीक्षा प्रश्न निर्माता हो। केवल इस डेटा का उपयोग करो: प्रश्न='{orig_q}', सही_उत्तर='{correct_val}'।
    स्टाइल: {prompts[mode]}
    नियम:
    1. भाषा: केवल उच्च स्तरीय देवनागरी हिंदी।
    2. अपना कोई बाहरी ज्ञान न जोड़ें।
    3. आउटपुट केवल JSON हो: {{"q": "नया सवाल", "o": ["V1", "V2", "V3", "V4"], "a": index}}
    """
    
    try:
        # यहाँ 'gemini-1.5-flash' सबसे भरोसेमंद मॉडल है
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-1.5-flash',
            contents=instruction,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.0, # हाई टेम्परेचर मतलब हर बार नया जवाब
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
            )
        )
        
        # क्लीनिंग लॉजिक
        res_text = response.text.strip()
        if "```json" in res_text:
            res_text = re.search(r'```json\s*(.*?)\s*```', res_text, re.DOTALL).group(1)
        
        return json.loads(res_text)
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        return None

# --- Button Handling ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ कृपया फाइल दोबारा भेजें।")

    await query.edit_message_text(text="⚡ AI दिमाग लगा रहा है... भाषा बदल रहा है...")
    
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
            await context.bot.send_message(query.message.chat_id, "तैयार?", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, f"⚠️ एरर: {e}")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ AI मॉडल ने जवाब देने से मना कर दिया। दोबारा कोशिश करें।")

# --- Main Logic ---
async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("फाइल भेजें और रट्टा छोड़ें!")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_click))
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main())
    except: pass
