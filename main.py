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

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
SESSION_TTL = 3600  # 1 hour in seconds

_SESSIONS: dict[str, dict] = {}


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


def is_youtube_url(text: str) -> bool:
    return bool(YOUTUBE_REGEX.search(text))


def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_REGEX.search(text)
    return match.group(0) if match else None


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
        "👋 أهلاً بك في بوت تحميل يوتيوب!\n\n"
        "📌 كيفية الاستخدام:\n"
        "أرسل رابط فيديو يوتيوب وسأعرض لك خيارات التحميل.\n\n"
        "🎥 يمكنك تحميل الفيديو (حتى 50 ميجابايت)\n"
        "🎵 أو تحميل الصوت بصيغة MP3"
    )
    await update.message.reply_text(welcome)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if not is_youtube_url(text):
        await update.message.reply_text(
            "❌ يرجى إرسال رابط يوتيوب صحيح.\n"
            "مثال: https://www.youtube.com/watch?v=xxxxx"
        )
        return

    url = extract_youtube_url(text)
    await update.message.reply_text("⏳ جاري جلب معلومات الفيديو...")

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, get_video_info, url)
    if not info:
        await update.message.reply_text(
            "❌ تعذّر جلب معلومات الفيديو. تأكد من صحة الرابط وحاول مجدداً."
        )
        return

    title = info.get("title", "غير معروف")
    duration = info.get("duration", 0)
    duration_str = format_duration(duration) if duration else "غير معروف"

    _cleanup_sessions()
    session_id = str(uuid.uuid4())
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    _SESSIONS[session_id] = {"url": url, "title": title, "ts": time.time(), "user_id": user_id, "chat_id": chat_id}

    keyboard = [
        [
            InlineKeyboardButton("🎥 تحميل فيديو", callback_data=f"video:{session_id}"),
            InlineKeyboardButton("🎵 تحميل صوت MP3", callback_data=f"audio:{session_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    safe_title = escape_markdown(title)
    caption = (
        f"📹 *{safe_title}*\n"
        f"⏱ المدة: {duration_str}\n\n"
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

    if action == "video":
        await _download_video(query, context, url, title)
    elif action == "audio":
        await _download_audio(query, context, url, title)
    else:
        await query.edit_message_text("❌ طلب غير معروف. أرسل الرابط مجدداً.")


async def _download_video(query, context, url: str, title: str) -> None:
    await query.edit_message_text("⏳ جاري تحميل الفيديو، يرجى الانتظار...")
    await context.bot.send_chat_action(
        chat_id=query.message.chat_id,
        action="upload_video",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = {
            "format": (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[height<=1080]+bestaudio"
                "/best[height<=1080]"
                "/best"
            ),
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Video download error: {e}")
            await query.edit_message_text(
                "❌ حدث خطأ أثناء تحميل الفيديو. حاول مجدداً لاحقاً."
            )
            return

        all_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
        if not all_files:
            await query.edit_message_text(
                "❌ لم يتم العثور على الملف بعد التحميل. حاول مجدداً."
            )
            return

        mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
        candidates = mp4_files if mp4_files else all_files
        video_file = max(candidates, key=lambda f: f.stat().st_mtime)
        file_size = video_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد المسموح (50 ميجابايت).\n"
                "💡 يمكنك تحميل الصوت بصيغة MP3 بدلاً من ذلك."
            )
            return

        try:
            await query.edit_message_text("📤 جاري إرسال الفيديو...")
            with open(video_file, "rb") as f:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=f"🎥 {title}",
                    supports_streaming=True,
                )
            await query.delete_message()
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text(
                "❌ حدث خطأ أثناء إرسال الفيديو. حاول مجدداً لاحقاً."
            )


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
            await query.edit_message_text(
                "❌ حدث خطأ أثناء تحميل الصوت. حاول مجدداً لاحقاً."
            )
            return

        all_audio_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
        if not all_audio_files:
            await query.edit_message_text(
                "❌ لم يتم العثور على ملف الصوت. حاول مجدداً."
            )
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
            await query.edit_message_text(
                "❌ حدث خطأ أثناء إرسال ملف الصوت. حاول مجدداً لاحقاً."
            )


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
