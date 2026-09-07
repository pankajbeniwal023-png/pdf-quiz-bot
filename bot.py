import os
import json
import logging
import asyncio
import random
from aiohttp import web
from telegram import Update, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    PollAnswerHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from google import genai
from google.genai import types

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_URL = os.environ.get("RENDER_URL")

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# डेटा स्टोर
QUESTION_BANK = []
ASKED_QUESTION_IDS = set()  # पूछे जा चुके प्रश्नों के इंडेक्स
POLL_TRACKER = {}

# --- Gemini Rephraser Engine (Strict Unique Variations) ---
async def rephrase_question_with_ai(original_q: dict, mode: str):
    random_seed = random.randint(1000, 999999)
    
    prompt = f"""
तुम एक बहुत ही उच्च स्तर के परीक्षा नियंत्रक हो।
नीचे दिया गया प्रश्न एक मूल सामान्य ज्ञान प्रश्न है:
{json.dumps(original_q, ensure_ascii=False)}

**तुम्हारा काम:**
इस प्रश्न का मुख्य तथ्य/ज्ञान (Core Fact) वही रखना है, लेकिन इसे बिल्कुल नए और अनोखे अंदाज़ में फिर से लिखना है ताकि छात्र को रटा-रटाया सवाल न मिले।

**मोड (Mode) के नियम:**
1. यदि mode = 'statement':
   - प्रश्न को "कथन I" और "कथन II" या "कथन (Assertion)" और "कारण (Reason)" के रूप में लिखो।
   - विकल्पों में दो/चार कथन संबंधित निष्कर्ष दो।
2. यदि mode = 'twisted' या 'varied':
   - प्रश्न की भाषा, संरचना और शब्दावली को पूरी तरह बदलो।
   - प्रश्न को किसी परिस्थिति (Scenario) या विश्लेषणात्मक प्रश्न के रूप में घुमाकर पूछो।

**अनिवार्य शर्तें (Seed: {random_seed}):**
- मूल प्रश्न का जो उत्तर सही है, वही उत्तर विकल्प सूची में सही रहना चाहिए।
- आउटपुट केवल और केवल शुद्ध JSON Format में दो।

JSON Format:
{{
  "question": "नया घुमावदार या कथन-कारण वाला प्रश्न?",
  "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
  "answer": {original_q['answer']}
}}
"""

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9,  # अधिक विविधता (Variety) के लिए
            ),
        )
        data = json.loads(response.text.strip())
        return data
    except Exception as e:
        logger.error(f"Rephrase Error: {e}")
        return original_q

# --- बॉट कमांड्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧠 **स्मार्ट रिवीज़न Quiz Bot**\n\n"
        "यह बॉट कभी भी एक सवाल या भाषा को **दोबारा रिपीट नहीं करेगा**।\n\n"
        "1. सबसे पहले `.json` या `.txt` फ़ाइल भेजें।\n"
        "2. फिर अभ्यास के लिए ये कमांड्स प्रयोग करें:\n\n"
        "📌 `/quiz 10` - नए तरीके से पूछे गए 10 अनोखे सवाल\n"
        "📝 `/statement 10` - 100% कथन एवं कारण वाले सवाल\n"
        "🔄 `/twisted 10` - घुमावदार लॉजिकल सवाल\n"
        "🧹 `/reset` - पूछे गए प्रश्नों की हिस्ट्री साफ़ करने के लिए"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# फ़ाइल अपलोड हैंडलर
async def handle_questions_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global QUESTION_BANK, ASKED_QUESTION_IDS
    doc = update.message.document
    file_name = doc.file_name.lower()
    
    if not (file_name.endswith('.json') or file_name.endswith('.txt')):
        return await update.message.reply_text("❌ केवल .json या .txt फ़ाइल ही भेजें।")

    msg = await update.message.reply_text("📥 फ़ाइल लोड हो रही है...")
    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        
        text_data = content.decode('utf-8').strip()
        data = json.loads(text_data)

        if isinstance(data, list) and len(data) > 0:
            QUESTION_BANK = data
            ASKED_QUESTION_IDS.clear()  # नई फ़ाइल आने पर हिस्ट्री क्लियर
            await msg.edit_text(
                f"✅ **सफलतापूर्वक {len(QUESTION_BANK)} सवाल लोड हो गए!**\n\n"
                "अब अभ्यास शुरू करने के लिए `/quiz 10` या `/statement 5` टाइप करें।", 
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text("❌ फ़ाइल में डेटा सही JSON लिस्ट फॉर्मेट में नहीं है।")
    except Exception as e:
        logger.error(f"File Load Error: {e}")
        await msg.edit_text("❌ फ़ाइल पढ़ने में त्रुटि हुई। कृपया फॉर्मेट चेक करें।")

# क्विज़ सेशन (No-Repeat Logic)
async def start_quiz_session(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global QUESTION_BANK, ASKED_QUESTION_IDS
    if not QUESTION_BANK:
        return await update.message.reply_text("❌ पहले अपनी प्रश्नों वाली फ़ाइल बॉट को भेजें!")

    # अभी तक न पूछे गए प्रश्नों के इंडेक्स चुनना
    unasked_indices = [i for i in range(len(QUESTION_BANK)) if i not in ASKED_QUESTION_IDS]

    if not unasked_indices:
        return await update.message.reply_text(
            "🎉 **बधाई हो! आप अपनी फ़ाइल के सभी सवालों का अभ्यास कर चुके हैं!**\n\n"
            "दोबारा शुरुआत से नए अंदाज़ में शुरू करने के लिए `/reset` कमांड दें।"
        )

    count = 5
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])

    # जितने बचे हैं और जितनी मांग है, उनमें से रैंडम चुनाव
    selected_indices = random.sample(unasked_indices, min(count, len(unasked_indices)))
    
    msg = await update.message.reply_text(f"⚡ AI आपके लिए बिल्कुल **नए तरीके** से सवाल तैयार कर रहा है... ⏳", parse_mode="Markdown")

    processed_questions = []
    for idx in selected_indices:
        ASKED_QUESTION_IDS.add(idx)  # पूछे जा चुके सवालों में जोड़ें
        raw_q = QUESTION_BANK[idx]
        
        # हर बार सवाल को AI से Reframe करवाना अनिवार्य है
        rephrased = await rephrase_question_with_ai(raw_q, mode)
        processed_questions.append(rephrased)

    await msg.delete()

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    context.user_data.clear()
    context.user_data.update({
        "quiz": processed_questions,
        "idx": 0,
        "score": 0,
        "total": len(processed_questions),
        "busy": True
    })

    await send_next_quiz(context, chat_id, user_id)

# कमांड्स
async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="varied")

async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="statement")

async def twisted_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="twisted")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ASKED_QUESTION_IDS
    ASKED_QUESTION_IDS.clear()
    await update.message.reply_text("🧹 **हिस्ट्री रीसेट हो गई है!** अब सवाल बिल्कुल शुरुआत से पूछे जा सकेंगे।")

async def send_next_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    user_data = context.application.user_data.get(user_id)
    if not user_data or not user_data.get("busy"):
        return

    idx = user_data.get("idx", 0)
    quiz = user_data.get("quiz", [])
    total = user_data.get("total", 0)

    if idx >= total:
        score = user_data.get("score", 0)
        per = int((score / total) * 100) if total > 0 else 0
        remaining = len(QUESTION_BANK) - len(ASKED_QUESTION_IDS)
        res = (
            f"🎉 **क्विज़ समाप्त!**\n\n"
            f"✅ सही उत्तर: {score} / {total}\n"
            f"📊 आपका स्कोर: {per}%\n"
            f"📚 फ़ाइल में अभी **{remaining}** नए सवाल बाकी हैं।"
        )
        await context.bot.send_message(chat_id, res, parse_mode="Markdown")
        user_data["busy"] = False
        return

    q = quiz[idx]
    q_text = f"Q{idx + 1}/{total}. {q['question']}"
    options = q['options']
    correct_id = q['answer']

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=q_text,
        options=options,
        type=Poll.QUIZ,
        correct_option_id=correct_id,
        is_anonymous=False
    )

    user_data["idx"] = idx + 1
    POLL_TRACKER[msg.poll.id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "correct_option_id": correct_id
    }

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id

    if poll_id not in POLL_TRACKER:
        return

    tracker = POLL_TRACKER.pop(poll_id)
    user_id = tracker["user_id"]
    chat_id = tracker["chat_id"]
    correct_option_id = tracker["correct_option_id"]

    if not poll_answer.option_ids:
        return

    selected = poll_answer.option_ids[0]
    user_data = context.application.user_data.get(user_id)

    if user_data and user_data.get("busy"):
        if selected == correct_option_id:
            user_data["score"] += 1
        await send_next_quiz(context, chat_id, user_id)

# --- AIOHTTP Server & Webhook ---
async def main():
    ptb_app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("quiz", quiz_cmd))
    ptb_app.add_handler(CommandHandler("statement", statement_cmd))
    ptb_app.add_handler(CommandHandler("twisted", twisted_cmd))
    ptb_app.add_handler(CommandHandler("reset", reset_cmd))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_questions_file))
    ptb_app.add_handler(PollAnswerHandler(handle_poll_answer))

    await ptb_app.initialize()
    await ptb_app.start()

    webhook_url = f"{RENDER_URL}/{TOKEN}"
    await ptb_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)

    web_app = web.Application()

    async def telegram_webhook(request):
        try:
            data = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            await ptb_app.process_update(update)
        except Exception as e:
            logger.error(f"Error: {e}")
        return web.Response(text="OK")

    async def health_check(request):
        return web.Response(text="Bot Alive")

    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)
    web_app.router.add_get("/", health_check)

    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
