import os
import json
import random
import asyncio
import logging
import re
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

# AI Client Setup
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-2.5-flash"  # Active & Stable Fast Model

USER_DATA = {}

# --- Render Port Binding ---
async def handle_root(request):
    return web.Response(text="Guaranteed Quiz Bot Active")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- AI Question Rewriter (Zero Fail Logic) ---
async def get_ai_rewritten_question(orig_q, correct_val, mode):
    if not ai_client:
        return None

    prompts = {
        "mode_twisted": f"इस प्रश्न की भाषा को उच्च स्तरीय परीक्षा शैली (RPSC/RSMSSB) में बदलो। अर्थ वही रहे: '{orig_q}'",
        "mode_reverse": f"उत्तर '{correct_val}' है। इस उत्तर के आधार पर एक कठिन पहेलीनुमा प्रश्न बनाओ जो मूल प्रश्न '{orig_q}' पर आधारित हो।",
        "mode_statement": f"कथन (Assertion) बनाओ मूल प्रश्न: '{orig_q}' से, और उसका सटीक कारण (Reason) लिखो जो सही उत्तर '{correct_val}' को सिद्ध करता हो।"
    }

    prompt = f"""
तुम राजस्थान प्रतियोगी परीक्षा विशेषज्ञ हो।
कार्यान्वयन: {prompts[mode]}
नियम: केवल एक नया सवाल या कथन-कारण लिखकर शुद्ध JSON आउटपुट दो।
JSON प्रारूप: {{"new_question": "यहाँ नया प्रश्न या कथन लिखें"}}
"""

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.8,
            )
        )
        res_text = response.text.strip()
        if "```json" in res_text:
            match = re.search(r'```json\s*(.*?)\s*```', res_text, re.DOTALL)
            if match:
                res_text = match.group(1)
        data = json.loads(res_text)
        return data.get("new_question")
    except Exception as e:
        logger.error(f"AI API Fail (Fallback activated): {e}")
        return None

# --- Smart Question Builder ---
async def build_question(user_id, mode):
    item = random.choice(USER_DATA[user_id])
    orig_q = item.get('question', '').strip()
    options = list(item.get('options', []))
    correct_idx = item.get('answer', 0)

    if not orig_q or not options:
        return None

    correct_val = options[correct_idx] if correct_idx < len(options) else options[0]

    # Step 1: AI से केवल प्रश्न की भाषा बदलवाएं
    ai_q = await get_ai_rewritten_question(orig_q, correct_val, mode)

    # Step 2: अगर AI सफल रहा तो वो सवाल लें, नहीं तो बैकअप पैटर्न (Guarantee Logic)
    if ai_q:
        final_q = ai_q
    else:
        # Fallback Options (अगर AI का सर्वर डाउन भी हो तो ये चलेगा)
        if mode == "mode_twisted":
            final_q = f"परीक्षा दृष्टिकोण से विचार कीजिए: {orig_q}"
        elif mode == "mode_reverse":
            final_q = f"यदि उत्तर '{correct_val}' है, तो यह किस संदर्भ से संबंधित है?"
        else:
            final_q = f"कथन: {orig_q}\n(तर्क सही उत्तर '{correct_val}' पर आधारित है)"

    # Step 3: विकल्पों का क्रम randomly बदलें (Shuffling)
    shuffled_options = options.copy()
    random.shuffle(shuffled_options)
    new_correct_idx = shuffled_options.index(correct_val)

    return {
        "q": final_q,
        "o": shuffled_options,
        "a": new_correct_idx
    }

# --- File Handling ---
async def handle_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list):
                    data = data[key]
                    break

        USER_DATA[user_id] = data

        keyboard = [
            [InlineKeyboardButton("🔄 कठिन भाषा (Twisted)", callback_data="mode_twisted")],
            [InlineKeyboardButton("🔄 उल्टा क्विज़ (Reverse)", callback_data="mode_reverse")],
            [InlineKeyboardButton("📝 कथन/कारण (Statement)", callback_data="mode_statement")]
        ]
        await update.message.reply_text(
            f"✅ {len(data)} सवाल लोड हुए!\n\nAI हर बार प्रश्न का तरीका बदलेगा।", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await update.message.reply_text("❌ JSON फाइल का फॉर्मेट सही नहीं है।")

# --- Button Handler ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ फाइल दोबारा भेजें।")

    # सोचने वाला मैसेज
    await query.edit_message_text(text="⚡ AI नया सवाल तैयार कर रहा है...")

    q_data = await build_question(user_id, mode)

    if q_data:
        try:
            await context.bot.send_poll(
                chat_id=query.message.chat_id,
                question=str(q_data['q'])[:300],
                options=[str(o)[:100] for o in q_data['o']],
                correct_option_id=int(q_data['a']),
                type=Poll.QUIZ,
                is_anonymous=False
            )
            keyboard = [[InlineKeyboardButton("अगला नया सवाल ➡️", callback_data=mode)]]
            await context.bot.send_message(query.message.chat_id, "तैयार?", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Poll Error: {e}")
            await context.bot.send_message(query.message.chat_id, "⚠️ पोल भेजने में समस्या आई।")
    else:
        await context.bot.send_message(query.message.chat_id, "❌ सवाल तैयार नहीं हो सका।")

# --- Main ---
async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("अपनी JSON फाइल भेजें!")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_click))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
