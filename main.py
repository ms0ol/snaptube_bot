import os
import logging
import re
import asyncio
import tempfile
import time
import uuid
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

YOUTUBE_REGEX = re.compile(
    r"(https?://)?"
    r"(www\.|m\.|music\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"[\w\-]+"
    r"(\?[\w=&\-%]*)?"
)

TIKTOK_REGEX = re.compile(
    r"(https?://)?"
    r"(www\.|vm\.|vt\.)?"
    r"tiktok\.com/"
    r"(@[\w\.\-]+/video/\d+|v/\d+|[\w\-]+)"
    r"(\?[\w=&\-%]*)?"
)

INSTAGRAM_REGEX = re.compile(
    r"(https?://)?"
    r"(www\.)?"
    r"instagram\.com/"
    r"(p|reel|tv|stories)/[\w\-]+"
    r"(/?)(\?[\w=&\-%]*)?"
)

PINTEREST_REGEX = re.compile(
    r"(https?://)?"
    r"(www\.|[a-z]{2}\.)?"
    r"pinterest\.(com|[a-z]{2,3})/"
    r"pin/[\d]+"
    r"(\?[\w=&\-%]*)?"
)

MAX_FILE_SIZE = 50 * 1024 * 1024
SESSION_TTL = 3600

_SESSIONS: dict[str, dict] = {}

PLATFORM_INFO = {
    "youtube":   {"name": "يوتيوب",    "icon": "▶️",  "supports_audio": True},
    "tiktok":    {"name": "تيك توك",   "icon": "🎵",  "supports_audio": False},
    "instagram": {"name": "إنستقرام",  "icon": "📸",  "supports_audio": False},
    "pinterest": {"name": "بينترست",   "icon": "📌",  "supports_audio": False},
}


def _cleanup_sessions() -> None:
    now = time.time()
    expired = [sid for sid, s in _SESSIONS.items() if now - s.get("ts", 0) > SESSION_TTL]
    for sid in expired:
        del _SESSIONS[sid]
    if expired:
        logger.debug(f"Cleaned up {len(expired)} expired session(s)")


def escape_markdown(text: str) -> str:
    escape_chars = r"\_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


def detect_platform(text: str) -> tuple[str | None, str | None]:
    """Returns (platform, url) or (None, None)"""
    if m := YOUTUBE_REGEX.search(text):
        return "youtube", m.group(0)
    if m := TIKTOK_REGEX.search(text):
        url = m.group(0)
        if not url.startswith("http"):
            url = "https://" + url
        return "tiktok", url
    if m := INSTAGRAM_REGEX.search(text):
        url = m.group(0)
        if not url.startswith("http"):
            url = "https://" + url
        return "instagram", url
    if m := PINTEREST_REGEX.search(text):
        url = m.group(0)
        if not url.startswith("http"):
            url = "https://" + url
        return "pinterest", url
    return None, None


def get_video_info(url: str) -> dict | None:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error fetching video info: {e}")
        return None


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}ث"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}د {secs}ث"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}س {mins}د {secs}ث"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome = (
        "👋 أهلاً بك في بوت التحميل الذكي!\n\n"
        "📌 المنصات المدعومة:\n"
        "▶️ يوتيوب — فيديو حتى 1080p أو MP3\n"
        "🎵 تيك توك — بدون علامة مائية\n"
        "📸 إنستقرام — ريلز وصور وفيديوهات\n"
        "📌 بينترست — فيديوهات وصور\n\n"
        "💡 فقط أرسل الرابط وسأتولى الباقي!"
    )
    await update.message.reply_text(welcome)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    platform, url = detect_platform(text)

    if not platform:
        await update.message.reply_text(
            "❌ الرابط غير مدعوم.\n\n"
            "المنصات المدعومة:\n"
            "▶️ يوتيوب\n"
            "🎵 تيك توك\n"
            "📸 إنستقرام\n"
            "📌 بينترست"
        )
        return

    pinfo = PLATFORM_INFO[platform]
    await update.message.reply_text(f"⏳ جاري جلب معلومات {pinfo['icon']} {pinfo['name']}...")

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, get_video_info, url)
    if not info:
        await update.message.reply_text(
            "❌ تعذّر جلب معلومات المحتوى. تأكد من صحة الرابط وحاول مجدداً."
        )
        return

    title = info.get("title", "بدون عنوان")
    duration = info.get("duration", 0)
    duration_str = format_duration(duration) if duration else "—"

    _cleanup_sessions()
    session_id = str(uuid.uuid4())
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    _SESSIONS[session_id] = {
        "url": url,
        "title": title,
        "platform": platform,
        "ts": time.time(),
        "user_id": user_id,
        "chat_id": chat_id,
    }

    keyboard = []
    if pinfo["supports_audio"]:
        keyboard.append([
            InlineKeyboardButton("🎥 تحميل فيديو", callback_data=f"video:{session_id}"),
            InlineKeyboardButton("🎵 تحميل صوت MP3", callback_data=f"audio:{session_id}"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("⬇️ تحميل", callback_data=f"video:{session_id}"),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    safe_title = escape_markdown(title)
    safe_duration = escape_markdown(duration_str)
    safe_platform = escape_markdown(pinfo["name"])

    caption = (
        f"{pinfo['icon']} *{safe_title}*\n"
        f"📡 المنصة: {safe_platform}\n"
        f"⏱ المدة: {safe_duration}\n\n"
        "اختر نوع التحميل:"
    )
    await update.message.reply_text(
        caption,
        reply_markup=reply_markup,
        parse_mode="MarkdownV2",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if ":" not in data:
        await query.edit_message_text("❌ طلب غير صالح. أرسل الرابط مجدداً.")
        return

    action, session_id = data.split(":", 1)
    session = _SESSIONS.get(session_id)
    if not session:
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return

    requester_id = query.from_user.id
    if session.get("user_id") and session["user_id"] != requester_id:
        await query.answer("❌ هذا الطلب ليس لك.", show_alert=True)
        return

    if time.time() - session.get("ts", 0) > SESSION_TTL:
        del _SESSIONS[session_id]
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return

    url = session["url"]
    title = session["title"]
    platform = session.get("platform", "youtube")

    if action == "video":
        await _download_video(query, context, url, title, platform)
    elif action == "audio":
        await _download_audio(query, context, url, title)
    else:
        await query.edit_message_text("❌ طلب غير معروف. أرسل الرابط مجدداً.")


def _build_video_opts(platform: str, output_template: str) -> dict:
    base = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }

    if platform == "tiktok":
        base["format"] = "download_addr/bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best"
        base["extractor_args"] = {"tiktok": {"app_name": ["trill"]}}
    elif platform == "instagram":
        base["format"] = "bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best"
    elif platform == "pinterest":
        base["format"] = "bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best"
    else:
        base["format"] = (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=1080]+bestaudio"
            "/best[height<=1080]"
            "/best"
        )

    return base


async def _download_video(query, context, url: str, title: str, platform: str) -> None:
    pinfo = PLATFORM_INFO.get(platform, PLATFORM_INFO["youtube"])
    await query.edit_message_text(f"⏳ جاري تحميل {pinfo['icon']} {pinfo['name']}، يرجى الانتظار...")
    await context.bot.send_chat_action(
        chat_id=query.message.chat_id,
        action="upload_video",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = _build_video_opts(platform, output_template)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Video download error ({platform}): {e}")
            await query.edit_message_text(
                "❌ حدث خطأ أثناء التحميل. حاول مجدداً لاحقاً."
            )
            return

        all_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
        if not all_files:
            await query.edit_message_text("❌ لم يتم العثور على الملف بعد التحميل. حاول مجدداً.")
            return

        mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
        candidates = mp4_files if mp4_files else all_files
        video_file = max(candidates, key=lambda f: f.stat().st_size)
        file_size = video_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم الملف ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد المسموح (50 ميجابايت)."
            )
            return

        try:
            await query.edit_message_text("📤 جاري إرسال الملف...")
            with open(video_file, "rb") as f:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=f"{pinfo['icon']} {title}",
                    supports_streaming=True,
                )
            await query.delete_message()
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء إرسال الملف. حاول مجدداً لاحقاً.")


async def _download_audio(query, context, url: str, title: str) -> None:
    await query.edit_message_text("⏳ جاري تحميل وتحويل الصوت إلى MP3، يرجى الانتظار...")
    await context.bot.send_chat_action(
        chat_id=query.message.chat_id,
        action="upload_document",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Audio download error: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء تحميل الصوت. حاول مجدداً لاحقاً.")
            return

        all_audio_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
        if not all_audio_files:
            await query.edit_message_text("❌ لم يتم العثور على ملف الصوت. حاول مجدداً.")
            return

        mp3_files = [f for f in all_audio_files if f.suffix.lower() == ".mp3"]
        audio_candidates = mp3_files if mp3_files else all_audio_files
        audio_file = max(audio_candidates, key=lambda f: f.stat().st_mtime)
        file_size = audio_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم ملف الصوت ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد المسموح (50 ميجابايت)."
            )
            return

        try:
            await query.edit_message_text("📤 جاري إرسال ملف الصوت...")
            with open(audio_file, "rb") as f:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=f,
                    title=title,
                    caption=f"🎵 {title}",
                )
            await query.delete_message()
        except Exception as e:
            logger.error(f"Error sending audio: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء إرسال ملف الصوت. حاول مجدداً لاحقاً.")


def _run_ydl(ydl_opts: dict, url: str):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.critical(
            "BOT_TOKEN is not set. "
            "Please add it to Replit Secrets: go to the Secrets tab and add BOT_TOKEN with your Telegram bot token from @BotFather."
        )
        raise RuntimeError("BOT_TOKEN environment variable is not set!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started. Polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
