import os
import re
import logging
import asyncio
from pathlib import Path
from threading import Thread

from flask import Flask, request, abort

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import yt_dlp

# =========================
# Render / Env config
# =========================
PORT = int(os.getenv("PORT", "10000"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # مثال: https://telegram-bot-85nr.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # مسار سري للويبهوك
MONGODB_URI = os.getenv("MONGODB_URI")  # اختياري

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في Environment Variables على Render.")
if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL غير موجود. ضعه في Render Environment.")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET غير موجود. ضعه في Render Environment.")

# =========================
# Paths
# =========================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_PATH = BASE_DIR / "youtube_cookies.txt"

# =========================
# Logging (HIDE TOKEN)
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("telegram_bot")

# ✅ اقفل لوغز httpx/httpcore نهائياً (حتى ما يظهر التوكن بأي سطر)
for noisy in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
    lg = logging.getLogger(noisy)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    lg.disabled = True

# ✅ خفف لوغز تيليجرام
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# (اختياري) خفف لوغز Flask/Werkzeug
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# =========================
# Flask app
# =========================
app = Flask(__name__)

# =========================
# Telegram Application
# =========================
application = Application.builder().token(BOT_TOKEN).build()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name)
    return name[:160] if len(name) > 160 else name


def find_downloaded_file(info: dict) -> Path | None:
    req = info.get("requested_downloads")
    if isinstance(req, list) and req:
        fp = req[0].get("filepath") or req[0].get("filename")
        if fp:
            p = Path(fp)
            if p.exists():
                return p

    fn = info.get("_filename")
    if fn:
        p = Path(fn)
        if p.exists():
            return p

    return None


def build_ydl_opts() -> dict:
    fmt = "best[ext=mp4]/best"
    opts = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 3,
        "restrictfilenames": False,
    }

    if COOKIES_PATH.exists():
        opts["cookiefile"] = str(COOKIES_PATH)
        logger.info("✅ Using YouTube cookies file.")
    else:
        logger.warning("⚠️ No youtube_cookies.txt found. Some YouTube links may require cookies.")

    # TikTok: يساعد لو curl-cffi موجود
    opts["impersonate"] = "chrome"
    return opts


async def run_yt_dlp_download(url: str) -> dict:
    ydl_opts = build_ydl_opts()

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    return await asyncio.to_thread(_download)


# =========================
# Bot handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلا 👋\n"
        "ابعثلي رابط فيديو (YouTube / TikTok / إلخ) وأنا بحاول نزّله.\n"
        "ملاحظة: بعض روابط YouTube تحتاج cookies."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "1) ابعت رابط الفيديو مباشرة.\n"
        "2) انتظر شوي لحد ما يخلص التحميل.\n\n"
        "إذا YouTube قال confirm not a bot: لازم ملف youtube_cookies.txt كـ Secret File على Render."
    )


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("ابعت رابط صحيح يبدأ بـ http أو https.")
        return

    msg = await update.message.reply_text("⏳ عم حمّل الفيديو…")

    try:
        info = await run_yt_dlp_download(url)

        title = safe_filename(info.get("title") or "video")
        file_path = find_downloaded_file(info)

        if not file_path:
            await msg.edit_text(f"✅ تم التحميل: {title}\nبس ما قدرت أحدد مسار الملف.")
            return

        try:
            await msg.edit_text(f"✅ تم التحميل: {title}\n⏳ عم أرسل الملف…")
            with file_path.open("rb") as f:
                await update.message.reply_document(document=f, filename=file_path.name)
            await msg.edit_text(f"✅ تم الإرسال بنجاح: {title}")
        except Exception as send_err:
            logger.error(f"❌ Failed to send file to Telegram: {send_err}")
            await msg.edit_text(
                f"✅ تم التحميل: {title}\n"
                "⚠️ بس فشل إرسال الملف (ممكن بسبب الحجم أو قيود تيليجرام).\n"
                "جرّب رابط آخر أو جودة أقل."
            )

    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        await msg.edit_text(
            "⚠️ فشل التحميل.\n"
            "إذا YouTube: غالباً يحتاج cookies.\n"
            "إذا TikTok: تأكد إن requirements فيها yt-dlp[default,curl-cffi] ومحدث."
        )


application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))


# =========================
# Flask routes
# =========================
@app.get("/")
def index():
    return "✅ Bot is running on Render!"


@app.post(f"/webhook/{WEBHOOK_SECRET}")
def webhook():
    data = request.get_json(silent=True)
    if not data:
        abort(400)

    try:
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
    except Exception as e:
        logger.error(f"❌ Error handling webhook: {e}")

    return "OK", 200


# =========================
# Startup
# =========================
async def main():
    logger.info("🚀 Starting Telegram bot...")

    await application.initialize()
    await application.start()

    webhook_full = f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}"
    await application.bot.set_webhook(url=webhook_full)

    logger.info("✅ Webhook set and bot is ready!")


if __name__ == "__main__":
    loop.create_task(main())
    Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    loop.run_forever()
