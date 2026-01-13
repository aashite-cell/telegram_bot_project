import os
import re
import logging
import asyncio
import random
from pathlib import Path
from threading import Thread

from flask import Flask, request, abort

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import yt_dlp

# =========================
# Render / Env config
# =========================
PORT = int(os.getenv("PORT", "10000"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")          # https://xxxx.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")    # سر لمسار الويبهوك

PROXY_URL = (os.getenv("PROXY_URL") or "").strip()  # اختياري (لو TikTok حجب IP)
TIKTOK_DEVICE_ID = (os.getenv("TIKTOK_DEVICE_ID") or "").strip()  # اختياري لتثبيت device_id

# مهم: فورمات اليوتيوب بدون ffmpeg (ملف واحد فقط)
# يفضّل mp4، وإذا ما موجود بياخد webm، وإذا ما موجود بياخد أي best واحد
YOUTUBE_FORMAT = (os.getenv("YOUTUBE_FORMAT") or "b[ext=mp4]/b[ext=webm]/b").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في Render Environment.")
if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL غير موجود في Render Environment.")
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET غير موجود في Render Environment.")

# =========================
# Paths
# =========================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ملف كوكيز واحد لكل المواقع (يوتيوب + تيك توك)
# ارفعه على Render كـ Secret File باسم cookies.txt
COOKIES_PATH = BASE_DIR / "cookies.txt"

# =========================
# Logging (خفّف ضجيج التوكن)
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("telegram_bot")

for noisy in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
    lg = logging.getLogger(noisy)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    lg.disabled = True

logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

STAMP = "v8-youtube-format-fix-2026-01-13"

# =========================
# Flask app + Telegram app
# =========================
app = Flask(__name__)
application = Application.builder().token(BOT_TOKEN).build()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# =========================
# Helpers
# =========================
WELCOME_TEXT = (
    "أهلا 👋\n"
    "أنا بوت تحميل فيديوهات.\n"
    "ابعث رابط YouTube أو TikTok وأنا بحاول نزّله وأرسله لك.\n"
    "اكتب /help للمساعدة."
)

def classify_url(url: str) -> str:
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "tiktok.com" in u:
        return "tiktok"
    return "other"

def safe_filename(name: str) -> str:
    name = (name or "").strip()
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

def _get_device_id() -> str:
    if TIKTOK_DEVICE_ID.isdigit() and len(TIKTOK_DEVICE_ID) >= 15:
        return TIKTOK_DEVICE_ID
    return "".join(str(random.randint(0, 9)) for _ in range(19))

def build_ydl_opts(url: str) -> dict:
    kind = classify_url(url)

    # ملاحظة مهمة:
    # "b" يعني Best single file (صوت+صورة بنفس الملف) -> ما يحتاج ffmpeg
    # على YouTube أحياناً mp4 غير متاح كملف واحد، فنعطي fallback إلى webm ثم أي best
    fmt = "best"
    if kind == "youtube":
        fmt = YOUTUBE_FORMAT
    elif kind == "tiktok":
        fmt = "best"

    opts = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 3,
        "nopart": True,
        "overwrites": True,
    }

    if PROXY_URL:
        opts["proxy"] = PROXY_URL

    # Cookies (عام لكل المواقع)
    logger.info(f"[{STAMP}] [cookies] exists? {COOKIES_PATH.exists()} path={COOKIES_PATH}")
    if COOKIES_PATH.exists():
        opts["cookiefile"] = str(COOKIES_PATH)
        logger.info(f"[{STAMP}] ✅ Using cookies.txt")

    # YouTube تحسينات: اختار أكثر من عميل لتقليل مشاكل "not a bot"
    if kind == "youtube":
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"]}}

    # TikTok: إعدادات API mode (ممكن تساعد)
    if kind == "tiktok":
        device_id = _get_device_id()
        opts.setdefault("extractor_args", {})
        opts["extractor_args"]["tiktok"] = {
            "api_hostname": "api22-normal-c-useast2a.tiktokv.com",
            "device_id": device_id,
            "aid": "1180",
            "manifest_app_version": "2023401020",
        }

    return opts

async def run_yt_dlp_download(url: str) -> dict:
    ydl_opts = build_ydl_opts(url)

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    return await asyncio.to_thread(_download)

# =========================
# Bot handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "1) ابعت رابط الفيديو مباشرة.\n"
        "2) انتظر لحد ما يخلص التحميل.\n\n"
        "ملاحظات:\n"
        "- YouTube أحياناً يرسل WebM بدل MP4، وهذا طبيعي.\n"
        "- إذا YouTube قال (not a bot): لازم cookies.txt من حساب YouTube.\n"
        "- إذا TikTok فشل: ممكن يحتاج PROXY_URL."
    )

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(WELCOME_TEXT)
        return

    kind = classify_url(url)
    msg = await update.message.reply_text("⏳ عم حمّل الفيديو…")

    try:
        info = await run_yt_dlp_download(url)

        title = safe_filename(info.get("title") or "video")
        file_path = find_downloaded_file(info)

        if not file_path:
            await msg.edit_text(f"✅ تم التحميل: {title}\nبس ما قدرت أحدد مسار الملف.")
            return

        await msg.edit_text(f"✅ تم التحميل: {title}\n⏳ عم أرسل الملف…")
        with file_path.open("rb") as f:
            await update.message.reply_document(document=f, filename=file_path.name)

        await msg.edit_text(f"✅ تم الإرسال بنجاح: {title}")

        # تنظيف بعد الإرسال لتوفير مساحة
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

    except Exception:
        logger.exception(f"[{STAMP}] ❌ Download error (full traceback):")

        if kind == "youtube":
            await msg.edit_text(
                "⚠️ فشل التحميل من YouTube.\n"
                "الأسباب الشائعة:\n"
                "1) كوكيز YouTube غير صحيحة/ناقصة.\n"
                "2) الفيديو يحتاج تسجيل دخول.\n\n"
                "جرّب تصدير الكوكيز من نفس المتصفح اللي أنت مسجل فيه على YouTube ثم ارفع cookies.txt من جديد."
            )
        elif kind == "tiktok":
            await msg.edit_text(
                "⚠️ فشل التحميل من TikTok.\n"
                "إذا استمر الفشل: جرّب PROXY_URL أو حدّث cookies.txt."
            )
        else:
            await msg.edit_text("⚠️ فشل التحميل. قد يكون الرابط غير مدعوم أو محمي.")

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
    except Exception:
        logger.exception(f"[{STAMP}] ❌ Error handling webhook (full traceback):")
    return "OK", 200

# =========================
# Startup
# =========================
async def main():
    logger.info(f"🚀 Starting Telegram bot... ({STAMP})")
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    logger.info(f"✅ Webhook set and bot is ready! ({STAMP})")

if __name__ == "__main__":
    loop.create_task(main())
    Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    loop.run_forever()
