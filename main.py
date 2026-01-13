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

# تفعيل سجل الأحداث
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("main")

# إصلاح event loop لتجنب أخطاء Render
nest_asyncio.apply()

# إعداد Flask
app = Flask(__name__)

# تأكيد وجود ملف الكوكيز
COOKIES_PATH = os.path.join(os.getcwd(), "youtube_cookies.txt")
if os.path.exists(COOKIES_PATH):
    logger.info(f"✅ Cookie file found at {COOKIES_PATH}")
else:
    logger.warning("⚠️ Cookie file NOT found inside Render project!")

# تعريف دوال البوت
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
            filename = ydl.prepare_filename(info)

        await update.message.reply_text(f"✅ تم التحميل بنجاح: {info['title']}")
    except Exception as e:
        logger.error(f"❌ Error downloading: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تحميل الفيديو. تأكد من الرابط وحاول مرة أخرى.")

# إنشاء التطبيق (Application)
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))

# تهيئة الـ loop الرئيسي
loop = asyncio.get_event_loop()

@app.route("/")
def index():
    return "✅ Bot is alive and running!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """مسار استقبال Webhook من Telegram"""
    try:
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, application.bot)

        # التأكد من أن التطبيق جاهز قبل معالجة التحديث
        if not application.running:
            logger.warning("⚠️ Application not ready yet, skipping update.")
            return "Bot not ready", 503

        loop.create_task(application.process_update(update))
        return "OK", 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return "Error", 500


async def main():
    logger.info("🚀 Starting Telegram bot with Webhook...")

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")

    logger.info("✅ Webhook set and bot is ready!")


if __name__ == "__main__":
    # تشغيل البوت
    loop.run_until_complete(main())

    # Flask يعمل فقط محليًا (وليس داخل Render)
    if os.getenv("RENDER") is None:
        app.run(host="0.0.0.0", port=PORT)
