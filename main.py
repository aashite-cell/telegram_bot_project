import os
import logging
import yt_dlp
import nest_asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import asyncio

# تفعيل الـ asyncio داخل بيئة Render
nest_asyncio.apply()

# إعداد سجل الأحداث (Logs)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بيانات البيئة من Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-bot-85nr.onrender.com")

# إعداد تطبيق Flask
app = Flask(__name__)

# دالة الترحيب
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! أرسل رابط فيديو من YouTube وسأقوم بتحميله لك!")

# دالة التحميل
async def download_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    await update.message.reply_text("⏳ يتم الآن تحميل الفيديو، يرجى الانتظار...")

    try:
        os.makedirs("downloads", exist_ok=True)

        # مسار ملف الكوكيز
        cookie_path = os.path.join(os.getcwd(), "youtube_cookies.txt")

        # فحص وجود الملف وكتابة النتيجة في الـ Logs
        if os.path.exists(cookie_path):
            logger.info(f"✅ Cookie file found at {cookie_path}")
        else:
            logger.warning("⚠️ Cookie file NOT found inside Render project!")

        ydl_opts = {
            "outtmpl": "downloads/%(title)s.%(ext)s",
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        # إذا الملف موجود، نضيفه للإعدادات
        if os.path.exists(cookie_path):
            ydl_opts["cookiefile"] = cookie_path

        # تحميل الفيديو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        # إرسال الفيديو للمستخدم
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(video=video_file)

        os.remove(file_path)

    except Exception as e:
        logger.error(f"❌ Error downloading: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تحميل الفيديو. تأكد من الرابط وجرب مرة أخرى.")

# تهيئة بوت تيليجرام
application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_handler))

# إعداد Webhook
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.run(application.process_update(update))
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "✅ Telegram bot is running!"

# تشغيل البوت
async def main():
    logger.info("🚀 Starting Telegram bot with Webhook...")
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")

    # تشغيل Flask بشكل غير متزامن
    loop = asyncio.get_running_loop()
    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=10000, use_reloader=False)).start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        # في حال حدث خطأ في الـ event loop
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
