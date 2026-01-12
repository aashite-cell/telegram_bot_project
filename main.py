"""
Free Telegram Video Downloader Bot (Webhook Version)
----------------------------------------------------
This version works completely free (no Stripe subscriptions).

✅ Fast Response using Webhook
✅ Works 24/7 with UptimeRobot
✅ Download videos from YouTube, TikTok, Instagram
✅ Compatible with Render servers
✅ Handles YouTube restrictions safely

Author: Anas Project 2026
"""

import asyncio
import logging
import os
import threading
from datetime import datetime
import re
import yt_dlp
from flask import Flask
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_URL = "https://telegram-bot-85nr.onrender.com"  # رابط خدمتك على Render ✅

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set.")

# ---------------- MongoDB ----------------
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client.get_default_database()
users_collection = db["users"]

# ---------------- Flask ----------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ Telegram Downloader Bot (Webhook Version) is running!"


# ---------------- Helpers ----------------
def clean_url(url: str) -> str:
    """ينظف الرابط من الزوائد مثل ?si= أو &feature="""
    url = re.sub(r"[?&]si=[^&]+", "", url)
    url = re.sub(r"[?&]feature=[^&]+", "", url)
    return url.strip()


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    users_collection.update_one(
        {"telegram_id": telegram_id},
        {"$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True,
    )

    keyboard = [[InlineKeyboardButton("🔗 أرسل رابط الفيديو الآن", callback_data="send_link")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 مرحبًا بك في بوت تحميل الفيديوهات!\n\n"
        "يمكنك تحميل مقاطع من يوتيوب أو تيك توك أو إنستغرام بسهولة.\n"
        "فقط أرسل الرابط مباشرة أو اضغط الزر أدناه 👇",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "send_link":
        await query.edit_message_text("📥 أرسل الآن رابط الفيديو الذي تريد تحميله:")


async def download_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = clean_url(update.message.text.strip())
    await update.message.reply_text("⏳ يتم الآن تحميل الفيديو، يرجى الانتظار...")

    try:
        os.makedirs("/tmp/downloads", exist_ok=True)
        ydl_opts = {
            "outtmpl": "/tmp/downloads/%(id)s.%(ext)s",
            "format": "best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "age_limit": 0,
            "extract_flat": False,
            "geo_bypass": True,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

        with open(file_path, "rb") as video_file:
            await update.message.reply_video(video=video_file)

        os.remove(file_path)
        logger.info(f"✅ تم تحميل الفيديو بنجاح: {url}")

    except Exception as e:
        logger.error(f"Download error: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء تحميل الفيديو. تأكد من الرابط وجرب مرة أخرى.")


# ---------------- Main ----------------
async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_handler))

    def run_flask():
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("🚀 Starting Telegram bot with Webhook...")

    await application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
    )


# ---------------- Run ----------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
