import os
import json
import random
import logging
import asyncio
from telegram import Update, Poll
from telegram.ext import Application, CommandHandler, MessageHandler, PollAnswerHandler, filters, ContextTypes
from google import genai
from google.genai import types

# लॉगिंग
logging.basicConfig(level=logging.INFO)

# API Keys
TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# बोट की याददाश्त (Data Bank)
RAW_DATA = [] # यहाँ आपके ओरिजिनल सवाल/फैक्ट्स रहेंगे

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("नमस्ते! अपनी JSON फाइल भेजें, फिर मैं हर बार नए सवाल बनाऊँगा।")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RAW_DATA
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    content = await file.download_as_bytearray()
    
    try:
        RAW_DATA = json.loads(content.decode('utf-8'))
        await update.message.reply_text(f"✅ {len(RAW_DATA)} फैक्ट्स लोड हो गए! अब /quiz टाइप करें।")
    except:
        await update.message.reply_text("❌ फाइल सही नहीं है।")

async def get_ai_question(fact):
    """AI को बोलकर एक नया सवाल बनवाने वाला फंक्शन"""
    prompt = f"""
    इस डेटा/तथ्य का उपयोग करके एक कठिन और नया बहुविकल्पीय प्रश्न (MCQ) हिंदी में बनाओ। 
    सवाल ऐसा हो कि छात्र को सोचना पड़े (Analytical)। विकल्पों को भी नया बनाओ।
    
    डेटा: {json.dumps(fact, ensure_ascii=False)}
    
    सख्ती से केवल इस JSON फॉर्मेट में जवाब दें:
    {{"question": "...", "options": ["A", "B", "C", "D"], "answer_index": 0}}
    """
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9)
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logging.error(f"AI Error: {e}")
        return None

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not RAW_DATA:
        return await update.message.reply_text("❌ पहले फाइल भेजें!")

    status_msg = await update.message.reply_text("🤔 AI सवाल सोच रहा है...")

    # रैंडम एक फैक्ट चुनना
    random_fact = random.choice(RAW_DATA)
    
    # AI से नया सवाल बनवाना (Live)
    q_data = await get_ai_question(random_fact)
    
    await status_msg.delete()

    if q_data:
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=q_data['question'][:300],
            options=q_data['options'][:10],
            type=Poll.QUIZ,
            correct_option_id=q_data['answer_index'],
            is_anonymous=False
        )
    else:
        await update.message.reply_text("❌ सवाल बनाने में दिक्कत हुई, दोबारा कोशिश करें।")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("बोट चालू है...")
    app.run_polling()

if __name__ == '__main__':
    main()
