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

QUESTION_BANK = []
ASKED_QUESTION_IDS = set()
POLL_TRACKER = {}

# --- Gemini Rephraser Engine (Updated Active Model) ---
async def rephrase_question_with_ai(original_q: dict, mode: str):
    orig_question = original_q.get("question", "")
    orig_options = original_q.get("options", [])
    correct_idx = original_q.get("answer", 0)
    
    if correct_idx < len(orig_options):
        correct_answer_text = orig_options[correct_idx]
    else:
        correct_answer_text = orig_options[0]

    random_seed = random.randint(10000, 999999)

    if mode == 'statement':
        instruction = f"""
तुम एक बहुत ही सख्त RPSC/UPSC परीक्षा विशेषज्ञ हो।
तुम्हें इस मूल प्रश्न को अनिवार्य रूप से "कथन (Assertion)" और "कारण (Reason)" वाले प्रश्न में बदलना है।

मूल प्रश्न: {orig_question}
सही उत्तर का मुख्य विचार: {correct_answer_text}

नियम:
1. प्रश्न को ऐसे लिखो:
   "कथन (A): [तथ्य]
   कारण (R): [कारण]"
2. मूल प्रश्न की भाषा बिल्कुल मत दोहराओ।
3. 4 विकल्प बनाओ (जैसे: A और R दोनों सही हैं..., A सही है R गलत है... आदि)।
4. 'correct_option_text' फ़ील्ड में वही विकल्प का पूरा टेक्स्ट डालो जो 100% सही उत्तर हो।
(Seed: {random_seed})
"""
    else:  # twisted / varied
        instruction = f"""
तुम एक परीक्षा विशेषज्ञ हो। इस प्रश्न की शब्दावली, वाक्य-रचना और भाषा को पूरी तरह से घुमाकर (Twisted/Scenario-based) नया बनाओ।

मूल प्रश्न: {orig_question}
सही उत्तर: {correct_answer_text}

नियम:
1. मूल प्रश्न के शब्दों को दोहराना सख्त मना है। प्रश्न का तरीका पूरी तरह नया और विश्लेषणात्मक (Analytical) होना चाहिए।
2. 4 नए विकल्प बनाओ।
3. 'correct_option_text' फ़ील्ड में सही विकल्प का पूरा टेक्स्ट डालो।
(Seed: {random_seed})
"""

    prompt = f"""{instruction}

आउटपुट केवल इस JSON प्रारूप में होना चाहिए:
{{
  "question": "यहाँ नया घुमावदार या कथन-कारण वाला प्रश्न लिखें",
  "options": ["विकल्प 1", "विकल्प 2", "विकल्प 3", "विकल्प 4"],
  "correct_option_text": "यहाँ इन 4 विकल्पों में से जो सही है उसका सटीक टेक्स्ट लिखें"
}}
"""

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash',  # 404 Not Found एरर फिक्स करने के लिए वर्किंग मॉडल
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.95,
            ),
        )
        data = json.loads(response.text.strip())
        
        # Dynamic Answer Index Fix
        new_options = data.get("options", [])
        correct_text = data.get("correct_option_text", "")
        
        new_answer_idx = 0
        if correct_text in new_options:
            new_answer_idx = new_options.index(correct_text)

        return {
            "question": data.get("question", orig_question),
            "options": new_options if len(new_options) == 4 else orig_options,
            "answer": new_answer_idx
        }
    except Exception as e:
        logger.error(f"Rephrase Error: {e}")
        return original_q

# --- बॉट कमांड्स ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🧠 **स्मार्ट रिवीज़न Quiz Bot**\n\n"
        "1. सबसे पहले अपनी `.json` या `.txt` फ़ाइल भेजें।\n"
        "2. फिर इन कमांड्स से अभ्यास करें:\n\n"
        "📌 `/quiz 10` - भाषा बदलकर नए तरीके के 10 सवाल\n"
        "📝 `/statement 10` - कथन और कारण (Assertion-Reason) वाले सवाल\n"
        "🔄 `/twisted 10` - घुमावदार लॉजिकल सवाल\n"
        "🧹 `/reset` - पूछे गए सवालों की हिस्ट्री साफ़ करें"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

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
            ASKED_QUESTION_IDS.clear()
            await msg.edit_text(
                f"✅ **सफलतापूर्वक {len(QUESTION_BANK)} सवाल लोड हो गए!**\n\n"
                "अब अभ्यास शुरू करने के लिए `/statement 5` या `/twisted 5` टाइप करें।", 
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text("❌ फ़ाइल में डेटा सही JSON लिस्ट फॉर्मेट में नहीं है।")
    except Exception as e:
        logger.error(f"File Load Error: {e}")
        await msg.edit_text("❌ फ़ाइल पढ़ने में त्रुटि हुई। कृपया फॉर्मेट चेक करें।")

async def start_quiz_session(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    global QUESTION_BANK, ASKED_QUESTION_IDS
    if not QUESTION_BANK:
        return await update.message.reply_text("❌ पहले अपनी प्रश्नों वाली फ़ाइल बॉट को भेजें!")

    unasked_indices = [i for i in range(len(QUESTION_BANK)) if i not in ASKED_QUESTION_IDS]

    if not unasked_indices:
        return await update.message.reply_text(
            "🎉 **आपकी फ़ाइल के सभी सवाल पूरे हो चुके हैं!**\n"
            "फिर से शुरू करने के लिए `/reset` कमांड दें।"
        )

    count = 5
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])

    selected_indices = random.sample(unasked_indices, min(count, len(unasked_indices)))
    
    msg = await update.message.reply_text(f"⚡ AI सवाल को `{mode.upper()}` फॉर्मेट में बदल रहा है... ⏳", parse_mode="Markdown")

    processed_questions = []
    for idx in selected_indices:
        ASKED_QUESTION_IDS.add(idx)
        raw_q = QUESTION_BANK[idx]
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

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="varied")

async def statement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="statement")

async def twisted_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_quiz_session(update, context, mode="twisted")

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ASKED_QUESTION_IDS
    ASKED_QUESTION_IDS.clear()
    await update.message.reply_text("🧹 **हिस्ट्री रीसेट हो गई है!**")

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
            f"📊 स्कोर: {per}%\n"
            f"📚 अभी **{remaining}** नए सवाल बाकी हैं।"
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

# --- Server setup ---
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
