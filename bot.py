import os
import json
import random
import logging
from aiohttp import web
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
USER_DATA = {}

# Web Server for Render Port Binding
async def handle_root(request):
    return web.Response(text="Logic Revision Bot Active")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# Logic Generator
def generate_smart_question(item, mode):
    orig_q = item.get('question', '').strip()
    options = list(item.get('options', []))
    correct_idx = item.get('answer', 0)
    
    if not orig_q or not options:
        return None

    correct_val = options[correct_idx] if correct_idx < len(options) else options[0]

    if mode == "mode_twisted":
        prefixes = [
            "गंभीरतापूर्वक विचार कीजिए: ",
            "परीक्षा दृष्टिकोण से सही तथ्य चुनिए: ",
            "निम्न संदर्भ में कौन-सा कथन सर्वथा उपयुक्त है? - "
        ]
        new_q = f"{random.choice(prefixes)}{orig_q}"

    elif mode == "mode_reverse":
        templates = [
            f"यदि अंतिम उत्तर '{correct_val}' है, तो यह किस संदर्भ या प्रश्न को निरूपित करता है?",
            f"पहचान कीजिए: वह कौन-सा उत्तर है जो मूल रूप से '{orig_q}' से संबंधित है?"
        ]
        new_q = random.choice(templates)

    elif mode == "mode_statement":
        reasons = [
            f"कथन (A): {orig_q}\nकारण (R): इसका सीधा संबंध '{correct_val}' के मूलभूत सिद्धांतों से है।",
            f"कथन (A): {orig_q}\nतर्क (R): दिए गए विकल्पों में से '{correct_val}' ही इसे पूर्णतः सिद्ध करता है।"
        ]
        new_q = random.choice(reasons)

    else:
        new_q = orig_q

    shuffled_options = options.copy()
    random.shuffle(shuffled_options)
    new_correct_idx = shuffled_options.index(correct_val)

    return {
        "q": new_q,
        "o": shuffled_options,
        "a": new_correct_idx
    }

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
            f"✅ {len(data)} सवाल सफलतापूर्वक लोड हुए!", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await update.message.reply_text("❌ JSON फाइल का फॉर्मेट सही नहीं है।")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    mode = query.data

    if user_id not in USER_DATA:
        return await query.message.reply_text("❌ फाइल दोबारा भेजें।")

    item = random.choice(USER_DATA[user_id])
    q_data = generate_smart_question(item, mode)

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

async def main():
    await start_web_server()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("JSON फाइल भेजें!")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_docs))
    app.add_handler(CallbackQueryHandler(button_click))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
