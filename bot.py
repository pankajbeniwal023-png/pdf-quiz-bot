import os
import json
import random
import logging
import re
import asyncio
from telegram import Update, Poll
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# लॉगिंग (एरर चेक करने के लिए)
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

# API Keys (Render/Environment से)
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ग्लोबल डेटा बैंक
USER_FACTS = [] 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 नमस्ते! मैं आपका **रट्टा-मारो-छोड़ो** बोट हूँ।\n\n"
        "1. अपनी तथ्यों वाली `.json` फाइल भेजें।\n"
        "2. मैं हर बार उसी फैक्ट से बिल्कुल **नया और घुमावदार** सवाल बनाऊंगा।\n"
        "3. शुरू करने के लिए /quiz टाइप करें।"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global USER_FACTS
    doc = update.message.document
    if not doc.file_name.lower().endswith('.json'):
        return await update.message.reply_text("❌ कृपया केवल .json फाइल भेजें।")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        USER_FACTS = json.loads(content.decode('utf-8'))
        await update.message.reply_text(f"✅ {len(USER_FACTS)} तथ्य लोड हो गए! अब आप /quiz खेल सकते हैं।")
    except Exception as e:
        await update.message.reply_text(f"❌ फाइल पढ़ने में गलती हुई: {e}")

async def get_dynamic_question(fact_item):
    """AI से एक सवाल को लाइव घुमावदार बनवाने का फंक्शन"""
    
    # AI को दिया जाने वाला सख्त निर्देश
    prompt = f"""
    तुम्हें इस तथ्य (Fact) का उपयोग करके एक कठिन और विश्लेषणात्मक (Analytical) MCQ प्रश्न बनाना है।
    
    तथ्य: {json.dumps(fact_item, ensure_ascii=False)}
    
    नियम:
    1. भाषा: केवल देवनागरी हिंदी।
    2. स्तर: बहुत कठिन और घुमावदार (ताकि रट्टा काम न आए)।
    3. प्रारूप: प्रश्न को ऐसे पूछें कि छात्र को गहराई से सोचना पड़े। 
    4. विकल्प: 4 विकल्प दें, जो आपस में मिलते-जुलते हों।
    
    सख्ती से केवल इस JSON फॉर्मेट में उत्तर दें:
    {{"q": "सवाल यहाँ", "o": ["विकल्प1", "विकल्प2", "विकल्प3", "विकल्प4"], "a": 0}}
    (जहाँ 'a' सही उत्तर का इंडेक्स है 0 से 3 के बीच)
    """

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash', # सबसे तेज़ मॉडल
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.0 # इसे 1.0 रखने से हर बार भाषा बदलेगी
            )
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logging.error(f"AI Generation Error: {e}")
        return None

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USER_FACTS:
        return await update.message.reply_text("❌ पहले अपनी JSON फाइल भेजें!")

    # यूजर को लगे कि बोट सोच रहा है
    status = await update.message.reply_text("🔎 AI नया सवाल तैयार कर रहा है...")

    # रैंडम एक तथ्य चुनना
    fact = random.choice(USER_FACTS)
    
    # AI से नया सवाल बनवाना
    q_data = await get_dynamic_question(fact)
    
    await status.delete() # 'सोच रहा है' वाला मैसेज हटा दें

    if q_data:
        try:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q_data['q'][:300], # टेलीग्राम की लिमिट
                options=[str(opt)[:100] for opt in q_data['o']],
                type=Poll.QUIZ,
                correct_option_id=int(q_data['a']),
                is_anonymous=False
            )
        except Exception as e:
            await update.message.reply_text("⚠️ सवाल भेजने में दिक्कत हुई, कृपया दोबारा /quiz दबाएँ।")
    else:
        await update.message.reply_text("❌ AI सवाल नहीं बना पाया। दोबारा कोशिश करें।")

def main():
    # Application बनाना
    app = Application.builder().token(TOKEN).build()

    # हैंडलर्स जोड़ना
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("✅ बोट चालू है और सवाल घुमाने के लिए तैयार है!")
    app.run_polling()

if __name__ == '__main__':
    main()
