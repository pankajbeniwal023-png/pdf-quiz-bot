import os
import json
import logging
import asyncio
import random
import re
from aiohttp import web
from telegram import Update, Poll
from telegram.ext import (
    Application, CommandHandler, PollAnswerHandler, MessageHandler, filters, ContextTypes
)
from google import genai
from google.genai import types

# लॉगिंग सेटिंग्स
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RENDER_URL = os.environ.get("RENDER_URL")

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# डेटा स्टोर करने के लिए
PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
ASKED_IDS = set()
POLL_TRACKER = {}

async def process_questions_with_ai(chunk):
    """AI को निर्देश: भाषा बदलो और विश्लेषणात्मक बनाओ"""
    prompt = f"""
    आप एक विशेषज्ञ परीक्षा प्रश्न पत्र निर्माता हैं। इन प्रश्नों को देवनागरी हिंदी में रूपांतरित करें:
    
    1. "statement": इसमें 'कथन और कारण' (Assertion-Reason) वाले प्रश्न बनाएं। भाषा कठिन और उच्च स्तरीय रखें।
    2. "twisted": इसमें प्रश्न की भाषा को पूरी तरह बदल दें (Rephrase)। शब्दावली ऐसी रखें कि छात्र को रट्टा मारने के बजाय सोचकर उत्तर देना पड़े।
    
    नियम:
    - भाषा केवल देवनागरी हिंदी होनी चाहिए।
    - हर प्रश्न में नए पर्यायवाची शब्दों का प्रयोग करें।
    - उत्तर का 'index' (answer) मूल डेटा जैसा ही रहना चाहिए।
    - केवल शुद्ध JSON एरे (Array) ही वापस भेजें।

    डेटा:
    {json.dumps(chunk, ensure_ascii=False)}
    """
    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model='gemini-2.0-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.0, # अधिकतम विविधता के लिए
            ),
        )
        # JSON निकालने का सुरक्षित तरीका
        text = response.text.strip()
        if "```json" in text:
            text = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL).group(1)
        return json.loads(text)
    except Exception as e:
        logger.error(f"AI Error: {e}")
        # फेल होने पर मूल सवाल ही वापस भेजें
        return [{"direct": q, "statement": q, "twisted": q} for q in chunk]

async def handle_questions_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PROCESSED_DATA, ASKED_IDS
    doc = update.message.document
    if not doc.file_name.lower().endswith(('.json', '.txt')):
        return await update.message.reply_text("❌ कृपया केवल .json या .txt फाइल भेजें।")

    status = await update.message.reply_text("⏳ AI आपके लिए नए और कठिन प्रश्न तैयार कर रहा है... इसमें कुछ समय लग सकता है।")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        data = json.loads(content.decode('utf-8'))

        # पुरानी याददाश्त साफ़ करें
        PROCESSED_DATA = {"direct": [], "statement": [], "twisted": []}
        ASKED_IDS.clear()

        # 5-5 सवालों के टुकड़ों में प्रोसेस करना (ताकि कभी फेल न हो)
        batch_size = 5
        for i in range(0, len(data), batch_size):
            batch = data[i:i+batch_size]
            await status.edit_text(f"🔄 प्रगति: {i}/{len(data)} प्रश्न तैयार हो चुके हैं...")
            results = await process_questions_with_ai(batch)
            
            for res in results:
                PROCESSED_DATA["direct"].append(res.get("direct", res))
                PROCESSED_DATA["statement"].append(res.get("statement", res))
                PROCESSED_DATA["twisted"].append(res.get("twisted", res))

        await status.edit_text("✅ **तैयारी पूरी हुई!**\nअब आप रट्टा नहीं मार पाएंगे।\n\nकमांड्स:\n/quiz - सामान्य\n/statement - कथन/कारण\n/twisted - घुमावदार भाषा")
    except Exception as e:
        await status.edit_text(f"❌ फाइल प्रोसेसिंग में त्रुटि: {e}")

async def start_quiz_session(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    bank = PROCESSED_DATA.get(mode, [])
    if not bank: return await update.message.reply_text("❌ पहले फाइल अपलोड करें!")

    available = [i for i in range(len(bank)) if i not in ASKED_IDS]
    if not available: return await update.message.reply_text("🎉 सभी प्रश्न समाप्त! /reset करें।")

    count = int(context.args[0]) if context.args and context.args[0].isdigit() else 5
    selected_indices = random.sample(available, min(count, len(available)))
    
    quiz_queue = [bank[i] for i in selected_indices]
    random.shuffle(quiz_queue)

    user_id = update.effective_user.id
    context.application.user_data[user_id] = {
        "quiz": quiz_queue, "idx": 0, "score": 0, "total": len(quiz_queue), "busy": True
    }
    await send_next_poll(context, update.effective_chat.id, user_id)

async def send_next_poll(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    ud = context.application.user_data.get(user_id)
    if not ud or ud["idx"] >= ud["total"]:
        if ud: await context.bot.send_message(chat_id, f"🏁 **क्विज़ समाप्त!**\nआपका स्कोर: {ud['score']}/{ud['total']}")
        return

    q = ud["quiz"][ud["idx"]]
    
    # विकल्पों को आपस में बदलना (Shuffling Options)
    opts = list(enumerate(q['options']))
    random.shuffle(opts)
    
    new_labels = [o[1] for o in opts]
    new_ans_id = next(i for i, o in enumerate(opts) if o[0] == q['answer'])

    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"प्रश्न {ud['idx']+1}: {q['question']}"[:300],
        options=[o[:100] for o in new_labels],
        type=Poll.QUIZ,
        correct_option_id=new_ans_id,
        is_anonymous=False
    )
    POLL_TRACKER[msg.poll.id] = {"uid": user_id, "cid": chat_id, "ans": new_ans_id}
    ud["idx"] += 1

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pa = update.poll_answer
    if pa.poll_id not in POLL_TRACKER: return
    
    track = POLL_TRACKER.pop(pa.poll_id)
    ud = context.application.user_data.get(track["uid"])
    if ud and ud["busy"]:
        if pa.option_ids[0] == track["ans"]: ud["score"] += 1
        await send_next_poll(context, track["cid"], track["uid"])

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ASKED_IDS.clear()
    await update.message.reply_text("🧹 इतिहास मिटा दिया गया है!")

async def main():
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("नमस्ते! फाइल भेजें।")))
    app.add_handler(CommandHandler("quiz", lambda u,c: start_quiz_session(u,c,"direct")))
    app.add_handler(CommandHandler("statement", lambda u,c: start_quiz_session(u,c,"statement")))
    app.add_handler(CommandHandler("twisted", lambda u,c: start_quiz_session(u,c,"twisted")))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_questions_file))
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    await app.initialize()
    await app.start()
    
    webhook_url = f"{RENDER_URL}/{TOKEN}"
    await app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    
    web_app = web.Application()
    web_app.router.add_post(f"/{TOKEN}", lambda r: telegram_webhook(r, app))
    web_app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await asyncio.Event().wait()

async def telegram_webhook(request, app):
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
    except Exception as e: logger.error(f"Webhook Error: {e}")
    return web.Response(text="OK")

if __name__ == '__main__':
    asyncio.run(main())
