import os, json, random, asyncio, logging, re
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google import genai
from google.genai import types

# लॉगिंग सेटअप
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# API Setup
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

USER_DATA = {} # फाइल्स स्टोर करने के लिए मेमोरी

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 **अल्ट्रा रिविजन बोट v2.0**\n\n"
        "अपनी तथ्यों (Facts) वाली `.json` फाइल भेजें, फिर रिविजन मोड चुनें।\n\n"
        "ये बोट रट्टा खत्म करने के लिए बनाया गया है! 🔥"
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
            [InlineKeyboardButton("🎯 MCQ Mode (घुमावदार)", callback_data="mode_mcq")],
            [InlineKeyboardButton("🧩 Fill-in-the-blanks (रिक्त स्थान)", callback_data="mode_fill")],
            [InlineKeyboardButton("❌ Liar Mode (सही/गलत पहचानें)", callback_data="mode_liar")],
            [InlineKeyboardButton("🔄 Reverse Quiz (उल्टा क्विज़)", callback_data="mode_reverse")]
        ]
        await status.edit_text(
            f"✅ {len(USER_DATA[user_id])} तथ्य लोड हो गए!\n\nअब रिविजन का तरीका चुनें:", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await status.edit_text(f"❌ एरर: {e}")

async def generate_smart_question(user_id, mode):
    """AI से अलग-अलग मोड के हिसाब से सवाल बनवाने वाला इंजन"""
    fact = random.choice(USER_DATA[user_id])
    
    prompts = {
        "mode_mcq": "इस फैक्ट से एक कठिन विश्लेषणात्मक MCQ सवाल देवनागरी हिंदी में बनाओ। भाषा घुमावदार हो।",
        "mode_fill": "इस फैक्ट की एक लाइन लिखो और सबसे महत्वपूर्ण शब्द की जगह '____' छोड़ दो। विकल्पों में 4 शब्द दो।",
        "mode_liar": "इस फैक्ट को थोड़ा बदलकर एक गलत स्टेटमेंट बनाओ (या कभी सही रहने दो)। विकल्पों में सिर्फ 'सही' और 'गलत' का ऑप्शन दो।",
        "mode_reverse": (
            "यह एक 'Reverse Quiz' है। \n"
            "1. सवाल (Question) में इस फैक्ट का मुख्य 'उत्तर/शब्द' (Main Entity/Answer) दिखाओ।\n"
            "2. विकल्पों (Options) में 4 अलग-अलग विवरण (Descriptions) दो, जिनमें से केवल एक उस शब्द के लिए सही हो।\n"
            "यूजर को पहचानना है कि यह उत्तर किस फैक्ट के लिए सही है।"
        )
    }

    prompt = f"""
    {prompts[mode]}
    
    तथ्य: {json.dumps(fact, ensure_ascii=False)}
    
    सख्ती से केवल इस JSON फॉर्मेट में जवाब दें:
    {{"q": "सवाल यहाँ", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": index_of_correct_option}}
    """
    
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9
            )
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logging.error(f"AI Generation Error: {e}")
        return None

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ पहले फाइल अपलोड करें!")

    await query.answer("AI सवाल तैयार कर रहा है...")
    
    q_data = await generate_smart_question(user_id, mode)
    
    if q_data:
        try:
            # पोल भेजना
            await context.bot.send_poll(
                chat_id=query.message.chat_id,
                question=q_data['q'][:300],
                options=[str(opt)[:100] for opt in q_data['o']],
                correct_option_id=int(q_data['a']),
                type=Poll.QUIZ,
                is_anonymous=False
            )
            
            # 'अगला सवाल' बटन ताकि लूप बना रहे
            keyboard = [[InlineKeyboardButton("अगला सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(
                chat_id=query.message.chat_id, 
                text=f"मोड: {mode.replace('mode_', '').upper()}\nउत्तर देने के बाद अगले सवाल पर जाएँ:", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "⚠️ पोल भेजने में दिक्कत हुई। दोबारा कोशिश करें।")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ AI सवाल नहीं बना पाया।")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("✅ स्मार्ट रिविजन बोट (With Reverse Mode) चालू है!")
    app.run_polling()

if __name__ == '__main__':
    main()
