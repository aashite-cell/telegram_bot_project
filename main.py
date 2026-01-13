import logging
import os
import nest_asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import asyncio

# إعدادات أساسية
BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت_الخاص_بك_هنا")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegram-bot-85nr.onrender.com")
PORT = int(os.getenv("PORT", 10000))

# إعداد السجل
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("main")

# إصلاح event loop في Render
nest_asyncio.apply()

# إعداد Flask
app = Flask(__name__)

# إعداد مسار الكوكيز
COOKIES_PATH = os.path.join(os.getcwd(), "youtube_cookies.txt")
if os.path.exists(COOKIES_PATH):
    logger.info(f"✅ Cookie file found at {COOKIES_PATH}")
else:
    logger.warning("⚠️ Cookie file NOT found inside Render project!")

# إنشاء حلقة asyncio واحدة لتشغيل كل المهام
loop = asyncio.get_event_loop()

# إنشاء التطبيق
application = Application.builder().token(BOT_TOKEN).build()

# دوال الأوامر
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 مرحبًا! أرسل لي رابط فيديو YouTube وسأقوم بتحميله لك.")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    await update.message.reply_text("⏳ جاري تحميل الفيديو، يرجى الانتظار...")
    try:
        ydl_opts = {
            "outtmpl": "downloads/%(title)s.%(ext)s",
            "format": "mp4",
        }
        if os.path.exists(COOKIES_PATH):
            ydl_opts["cookiefile"] = COOKIES_PATH

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        await update.message.reply_text(f"✅ تم التحميل بنجاح: {info['title']}")
    except Exception as e:
        logger.error(f"❌ Error downloading: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تحميل الفيديو. تأكد من الرابط وحاول مرة أخرى.")

# إضافة المعالجات
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))

@app.route("/")
def index():
    return "✅ Bot is running on Render!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """يستقبل التحديثات من Telegram"""
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)

        # استخدم loop الرئيسي بدلاً من asyncio.run()
        if application.running:
            asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        else:
            logger.warning("⚠️ Application not ready yet to handle update")

    except Exception as e:
        logger.error(f"❌ Error handling webhook: {e}")
    return "OK", 200


async def main():
    logger.info("🚀 Starting Telegram bot with Webhook...")

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")

    logger.info("✅ Webhook set and bot is ready!")

# تشغيل البوت
if __name__ == "__main__":
    # شغّل التهيئة داخل نفس الحلقة
    loop.create_task(main())

    # تشغيل Flask بدون asyncio.run()
    if os.getenv("RENDER") is None:
        app.run(host="0.0.0.0", port=PORT)
    else:
        # على Render نحتاج لتشغيل السيرفر داخل نفس الـ loop
        from threading import Thread
        Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
        loop.run_forever()
