import os, json, random, asyncio, logging
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# लॉगिंग (एरर चेक करने के लिए)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Environment Variables
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {}

# --- Render के लिए वेब सर्वर ---
async def handle_root(request): return web.Response(text="Bot Alive")
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# --- फाइल हैंडलर (JSON और TXT दोनों के लिए) ---
async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    
    # अब .json और .txt दोनों स्वीकार करेगा
    if not doc.file_name.lower().endswith(('.json', '.txt')):
        return await update.message.reply_text("❌ कृपया .json या .txt फाइल भेजें।")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        # स्मार्ट चेकिंग: अगर लिस्ट किसी key के अंदर है तो उसे निकालो
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list):
                    data = data[key]
                    break
        
        if not isinstance(data, list):
            return await update.message.reply_text("❌ फाइल का फॉर्मेट सही नहीं है।")

        USER_DATA[user_id] = data
        
        keyboard = [
            [InlineKeyboardButton("🎯 MCQ Mode", callback_data="mode_mcq")],
            [InlineKeyboardButton("🔄 Reverse Quiz (उल्टा)", callback_data="mode_reverse")],
            [InlineKeyboardButton("🧩 Fill Blanks", callback_data="mode_fill")]
        ]
        await update.message.reply_text(
            f"✅ **{len(data)} सवाल लोड हो गए!**\n\nअब नीचे से रिविजन का तरीका चुनें:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"File Error: {e}")
        await update.message.reply_text("❌ फाइल पढ़ने में गड़बड़ हुई। चेक करें कि वह सही JSON है या नहीं।")

# --- AI से सवाल बनवाना ---
async def generate_smart_question(user_id, mode):
    if user_id not in USER_DATA: return None
    
    item = random.choice(USER_DATA[user_id])
    original_q = item.get('question', '')
    options = item.get('options', [])
    ans_idx = item.get('answer', 0)
    correct_opt = options[ans_idx] if options else ""

    # AI के लिए प्रॉम्प्ट
    prompts = {
        "mode_mcq": f"इस सवाल को कठिन बनाओ: '{original_q}'। सही जवाब '{correct_opt}' ही रहे।",
        "mode_reverse": f"उल्टा क्विज़: सवाल में मुख्य शब्द '{correct_opt}' दिखाओ। विकल्पों में 4 विवरण दो जिसमें से एक '{original_q}' पर आधारित हो।",
        "mode_fill": f"वाक्य: '{original_q}'। उत्तर: '{correct_opt}'। इसे रिक्त स्थान वाले सवाल में बदलो।"
    }

    prompt = f"""
    तुम एक परीक्षा एक्सपर्ट हो। केवल इस डेटा का उपयोग करो: सवाल='{original_q}', जवाब='{correct_opt}'।
    कार्य: {prompts.get(mode, "MCQ बनाओ")}
    नियम: केवल देवनागरी हिंदी। 100% सही उत्तर। 
    JSON फॉर्मेट: {{"q": "सवाल", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": index}}
    """
    
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.8)
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logging.error(f"AI Error: {e}")
        return None

# --- बटन क्लिक हैंडलर ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ आपकी फाइल डिलीट हो गई है, कृपया दोबारा भेजें।")

    # बटन दबते ही मैसेज बदलें ताकि यूजर को पता चले कि काम हो रहा है
    await query.edit_message_text(text="🤔 AI सवाल तैयार कर रहा है... कृपया प्रतीक्षा करें।")
    
    q_data = await generate_smart_question(user_id, mode)
    
    if q_data:
        try:
            await context.bot.send_poll(
                chat_id=query.message.chat_id,
                question=q_data['q'][:300],
                options=[str(opt)[:100] for opt in q_data['o']],
                correct_option_id=int(q_data['a']),
                type=Poll.QUIZ,
                is_anonymous=False
            )
            # अगला सवाल बटन
            keyboard = [[InlineKeyboardButton("अगला सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "अगला सवाल पाने के लिए दबाएँ:", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "⚠️ पोल भेजने में तकनीकी समस्या आई।")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ AI जवाब नहीं दे पाया। दोबारा कोशिश करें।")

# --- मुख्य फंक्शन ---
async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("नमस्ते! अपनी फाइल भेजें।")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main())
    except: pass
