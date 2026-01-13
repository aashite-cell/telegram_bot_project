import os
import re
import logging
import asyncio
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")          # https://telegram-bot-85nr.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")    # سر لمسار الويبهوك
PROXY_URL = (os.getenv("PROXY_URL") or "").strip()  # اختياري لاحقًا إذا TikTok حجب IP السيرفر

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

# ✅ ملف كوكيز واحد لكل المواقع (يوتيوب + تيك توك)
# على Render ارفعه كـ Secret File باسم cookies.txt
COOKIES_PATH = BASE_DIR / "cookies.txt"

# =========================
# Logging (hide token logs)
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

def _fix_impersonate_for_python_api(opts: dict) -> None:
    if "impersonate" not in opts or opts["impersonate"] is None:
        return
    if isinstance(opts["impersonate"], str):
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str(opts["impersonate"].lower())
        except Exception:
            opts.pop("impersonate", None)

def build_ydl_opts(url: str) -> dict:
    kind = classify_url(url)

    opts = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 3,
    }

    # Proxy (اختياري)
    if PROXY_URL:
        opts["proxy"] = PROXY_URL

    # Cookies (عام لكل المواقع)
    if COOKIES_PATH.exists():
        opts["cookiefile"] = str(COOKIES_PATH)

    # YouTube improvements
    if kind == "youtube":
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"]}}

    # ✅ TikTok workaround مشهور لحالات "Unable to extract webpage video data"
    # مذكور في قضايا yt-dlp كتجربة حل. :contentReference[oaicite:3]{index=3}
    if kind == "tiktok":
        opts.setdefault("extractor_args", {})
        opts["extractor_args"]["tiktok"] = {
            "api_hostname": "api22-normal-c-useast2a.tiktokv.com"
        }

    # impersonation (مع تصحيح النوع)
    opts["impersonate"] = "chrome"
    _fix_impersonate_for_python_api(opts)

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
        "إذا TikTok فشل: أحيانًا يحتاج تحديث yt-dlp أو Proxy.\n"
        "إذا TikTok حساب خاص: يحتاج cookies لحساب عنده صلاحية. :contentReference[oaicite:4]{index=4}"
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

    except Exception:
        logger.exception("❌ Download error (full traceback):")

        if kind == "tiktok":
            await msg.edit_text(
                "⚠️ فشل التحميل من TikTok.\n"
                "إذا استمر الفشل حتى بعد التحديث، غالبًا TikTok حجب IP السيرفر.\n"
                "الحل وقتها: تفعيل PROXY_URL أو استخدام cookies."
            )
        elif kind == "youtube":
            await msg.edit_text(
                "⚠️ فشل التحميل من YouTube.\n"
                "إذا الفيديو محمي/يتطلب تسجيل دخول: استخدم cookies.txt."
            )
        else:
            await msg.edit_text("⚠️ فشل التحميل. قد يكون الرابط غير مدعوم أو محمي.")

# register handlers
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
        logger.exception("❌ Error handling webhook (full traceback):")
    return "OK", 200

# =========================
# Startup
# =========================
async def main():
    logger.info("🚀 Starting Telegram bot...")
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    logger.info("✅ Webhook set and bot is ready!")

if __name__ == "__main__":
    loop.create_task(main())
    Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    loop.run_forever()
