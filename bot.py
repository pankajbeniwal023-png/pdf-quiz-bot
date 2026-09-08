import os, json, random, asyncio, logging, re
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Config ---
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {} # फाइल्स स्टोर करने के लिए

# --- Render Alive Server ---
async def handle_root(request): return web.Response(text="Revision Bot is Live!")
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

        # आपके JSON फॉर्मेट (Key -> List) को हैंडल करना
        if isinstance(raw_data, dict):
            # पहला key ढूंढो जिसमें list हो
            for key in raw_data:
                if isinstance(raw_data[key], list):
                    USER_DATA[user_id] = raw_data[key]
                    break
        elif isinstance(raw_data, list):
            USER_DATA[user_id] = raw_data
        
        if user_id not in USER_DATA or not USER_DATA[user_id]:
            return await update.message.reply_text("❌ फाइल में सवाल नहीं मिले। फॉर्मेट चेक करें।")

        keyboard = [
            [InlineKeyboardButton("🎯 MCQ (भाषा बदलें)", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 Reverse Quiz (उल्टा)", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 Statement (कथन/कारण)", callback_data="mode_statement")],
            [InlineKeyboardButton("🧩 Fill Blanks (रिक्त स्थान)", callback_data="mode_fill")]
        ]
        await update.message.reply_text(
            f"✅ **{len(USER_DATA[user_id])} सवाल लोड हो गए!**\n\nअब रिविजन का स्टाइल चुनें (हर बार नया सवाल मिलेगा):",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("❌ फाइल पढ़ने में त्रुटि। पक्का करें कि फाइल सही JSON है।")

# --- AI Question Generator ---
async def generate_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    original_q = item.get('question', '')
    options = item.get('options', [])
    correct_opt = options[item.get('answer', 0)]

    prompts = {
        "mode_twisted": f"इस सवाल को कठिन हिंदी शब्दों और घुमावदार भाषा में दोबारा लिखो: '{original_q}'। उत्तर '{correct_opt}' ही रहे।",
        "mode_reverse": f"उल्टा क्विज़: सवाल में मुख्य शब्द '{correct_opt}' दिखाओ। विकल्पों में 4 विवरण (Descriptions) दो जिसमें से एक '{original_q}' से मेल खाता हो।",
        "mode_statement": f"कथन-कारण (Assertion-Reason) स्टाइल में सवाल बनाओ। कथन: '{original_q}', कारण: '{correct_opt}' के आधार पर।",
        "mode_fill": f"सवाल '{original_q}' का उपयोग करके एक 'रिक्त स्थान' वाला वाक्य बनाओ जहाँ '{correct_opt}' सही उत्तर हो।"
    }

    instruction = f"""
    तुम एक कठिन परीक्षा के पेपर सेटर हो। केवल इस डेटा का उपयोग करो: सवाल='{original_q}', उत्तर='{correct_opt}'।
    स्टाइल: {prompts[mode]}
    नियम: 
    1. भाषा देवनागरी हिंदी हो। 
    2. अपना कोई बाहर का ज्ञान न जोड़ें, जो डेटा में है वही रहे।
    3. विकल्प आपस में मिलते-जुलते (Confusing) बनाएं।
    4. JSON फॉर्मेट: {{"q": "सवाल", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": index}}
    """
    
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash',
            contents=instruction,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=1.0)
        )
        return json.loads(response.text.strip())
    except:
        # Fallback: अगर AI फेल हो तो ओरिजिनल सवाल दिखाएं
        return {"q": original_q, "o": options, "a": item.get('answer', 0)}

# --- Callback Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ पहले फाइल भेजें।")

    # मैसेज अपडेट ताकि यूजर को लगे काम हो रहा है
    await query.edit_message_text(text="⚡ AI नया सवाल तैयार कर रहा है...")
    
    q_data = await generate_question(user_id, mode)
    
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
            # अगला सवाल बटन ताकि लूप बना रहे
            nxt_kb = [[InlineKeyboardButton("अगला सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "तैयार रहें!", reply_markup=InlineKeyboardMarkup(nxt_kb))
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "⚠️ पोल भेजने में गड़बड़। कृपया फिर दबाएँ।")

# --- Main App ---
async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("नमस्ते! अपनी JSON/TXT फाइल भेजें।")))
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
