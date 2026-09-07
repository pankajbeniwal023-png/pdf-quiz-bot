import os, json, random, asyncio, logging
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Environment Variables (Render पर आपने जो डाले हैं वही इस्तेमाल होंगे)
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {} # अस्थायी मेमोरी

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 **अल्ट्रा रिविजन बोट**\n\n"
        "अपनी `.json` फाइल भेजें। मैं आपके सवालों को घुमाकर पूछूँगा ताकि रट्टा खत्म हो सके।\n"
        "⚠️ मैं केवल आपकी फाइल के जवाबों का ही उपयोग करूँगा।"
    )

async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    
    if not doc.file_name.lower().endswith('.json'):
        return await update.message.reply_text("❌ कृपया केवल .json फाइल भेजें।")

    status = await update.message.reply_text("📥 फाइल लोड हो रही है...")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        USER_DATA[user_id] = json.loads(content.decode('utf-8'))
        
        keyboard = [
            [InlineKeyboardButton("🎯 MCQ (घुमावदार)", callback_data="mode_mcq")],
            [InlineKeyboardButton("🧩 Fill-in-the-blanks", callback_data="mode_fill")],
            [InlineKeyboardButton("❌ Liar Mode (सही/गलत)", callback_data="mode_liar")],
            [InlineKeyboardButton("🔄 Reverse Quiz (उल्टा)", callback_data="mode_reverse")]
        ]
        await status.edit_text(
            f"✅ {len(USER_DATA[user_id])} सवाल लोड हुए!\nअब रिविजन का तरीका चुनें:", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await status.edit_text(f"❌ फाइल में गड़बड़ है। कृपया फॉर्मेट चेक करें।")

async def generate_smart_question(user_id, mode):
    """AI इंजन - केवल यूजर के डेटा का उपयोग करेगा"""
    item = random.choice(USER_DATA[user_id])
    
    # मूल सवाल और सही उत्तर निकालना
    original_q = item['question']
    correct_opt = item['options'][item['answer']]

    prompts = {
        "mode_mcq": f"इस सवाल को कठिन और नई हिंदी शब्दावली में दोबारा लिखो: '{original_q}'. सही जवाब '{correct_opt}' ही रहना चाहिए।",
        "mode_fill": f"सवाल '{original_q}' और जवाब '{correct_opt}' को मिलाकर एक 'Fill in the blank' वाक्य बनाओ। '{correct_opt}' की जगह ____ छोड़ दो।",
        "mode_liar": f"तथ्य: '{original_q}' का उत्तर '{correct_opt}' है। इस आधार पर एक सही या गलत स्टेटमेंट बनाओ।",
        "mode_reverse": f"यह उल्टा क्विज़ है। सवाल में मुख्य शब्द '{correct_opt}' लिखो। विकल्पों में 4 अलग-अलग विवरण दो, जिसमें से एक विवरण इस सवाल '{original_q}' पर आधारित हो।"
    }

    prompt = f"""
    तुम एक परीक्षा विशेषज्ञ हो। तुम्हें केवल नीचे दिए गए डेटा का उपयोग करना है। अपना कोई बाहरी ज्ञान न लगायें।
    डेटा: सवाल="{original_q}", सही_जवाब="{correct_opt}"
    
    कार्य: {prompts[mode]}
    
    नियम:
    1. भाषा: देवनागरी हिंदी।
    2. फॉर्मेट: केवल JSON में उत्तर दें: {{"q": "सवाल", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": सही_इंडेक्स}}
    """
    
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.7)
        )
        return json.loads(response.text.strip())
    except:
        return None

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA: return await query.answer("फाइल फिर से भेजें।")

    await query.answer("AI सवाल बना रहा है...")
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
            keyboard = [[InlineKeyboardButton("अगला सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "तैयार?", reply_markup=InlineKeyboardMarkup(keyboard))
        except:
            await context.bot.send_message(query.message.chat_id, "⚠️ कोशिश जारी है, /start दबाएँ।")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == '__main__':
    main()
