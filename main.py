import os
import logging
import re
import asyncio
import tempfile
import time
import uuid
from pathlib import Path

import httpx
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
    r"https?://"
    r"(www\.|vm\.|vt\.|m\.)?"
    r"tiktok\.com/"
    r"[\w@/\-\?=&\.]+"
    r"|"
    r"https?://vt\.tiktok\.com/[\w]+"
    r"|"
    r"https?://vm\.tiktok\.com/[\w]+"
)

INSTAGRAM_REGEX = re.compile(
    r"https?://"
    r"(www\.)?"
    r"instagram\.com/"
    r"(p|reel|tv|stories)/[\w\-]+"
    r"(/?)(\?[\w=&\-%]*)?"
)

PINTEREST_REGEX = re.compile(
    r"https?://"
    r"(?:(?:www\.|[a-z]{2}\.)?pinterest\.(?:com|[a-z]{2,3})/pin/[\d]+"
    r"|pin\.it/[\w]+"
    r")"
    r"(\?[\w=&\-%]*)?"
)

MAX_FILE_SIZE = 50 * 1024 * 1024
SESSION_TTL = 3600

_SESSIONS: dict[str, dict] = {}

PLATFORM_INFO = {
    "youtube":   {"name": "يوتيوب",   "icon": "▶️", "supports_audio": True},
    "tiktok":    {"name": "تيك توك",  "icon": "🎵", "supports_audio": False},
    "instagram": {"name": "إنستقرام", "icon": "📸", "supports_audio": False},
    "pinterest": {"name": "بينترست",  "icon": "📌", "supports_audio": False},
}

TIKWM_API = "https://tikwm.com/api/"
PINTEREST_WIDGETS_API = "https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={}"


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
    if m := YOUTUBE_REGEX.search(text):
        return "youtube", m.group(0)
    if m := TIKTOK_REGEX.search(text):
        return "tiktok", m.group(0)
    if m := INSTAGRAM_REGEX.search(text):
        return "instagram", m.group(0)
    if m := PINTEREST_REGEX.search(text):
        return "pinterest", m.group(0)
    return None, None


def get_video_info(url: str) -> dict | None:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Error fetching video info: {e}")
        return None


def get_tiktok_info(url: str) -> dict | None:
    try:
        r = httpx.post(
            TIKWM_API,
            data={"url": url, "hd": "1"},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        data = r.json()
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
    except Exception as e:
        logger.error(f"TikTok API error: {e}")
    return None


def get_pinterest_info(url: str) -> dict | None:
    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {"type": "video", "info": info}
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e)
        if "No video formats found" in err_str or "no video formats" in err_str.lower():
            # yt-dlp includes pin ID in error: "[Pinterest] PIN_ID: No video..."
            pin_id = _extract_pin_id_from_error(err_str) or _extract_pinterest_pin_id(url)
            if pin_id:
                return _get_pinterest_image(pin_id)
        logger.error(f"Pinterest yt-dlp error: {e}")
        return None
    except Exception as e:
        logger.error(f"Pinterest error: {e}")
        return None


def _extract_pin_id_from_error(err_str: str) -> str | None:
    m = re.search(r"\[Pinterest\]\s+(\d+):", err_str)
    return m.group(1) if m else None


def _extract_pinterest_pin_id(url: str) -> str | None:
    m = re.search(r"/pin/(\d+)", url)
    if m:
        return m.group(1)
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r"/pin/(\d+)", str(r.url))
        return m.group(1) if m else None
    except Exception:
        return None


def _get_pinterest_image(pin_id: str) -> dict | None:
    try:
        for attempt in range(3):
            r = httpx.get(
                PINTEREST_WIDGETS_API.format(pin_id),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15,
            )
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            break
        data = r.json()
        pins = data.get("data", [])
        if not pins:
            return None
        pin = pins[0]
        title = pin.get("description") or pin.get("title") or "بينترست"

        story = pin.get("story_pin_data")
        if story:
            for page in story.get("pages", []):
                vid = page.get("video")
                if vid:
                    video_list = vid.get("video_list", {})
                    for key in ("V_720P", "V_480P", "V_360P", "V_EXP4"):
                        if key in video_list:
                            video_url = video_list[key].get("url")
                            if video_url:
                                return {"type": "video_url", "url": video_url, "title": title}
                img = page.get("image", {})
                if img:
                    orig = img.get("images", {}).get("originals", {})
                    img_url = orig.get("url")
                    if img_url:
                        return {"type": "image", "url": img_url, "title": title}

        images = pin.get("images") or {}
        orig = images.get("originals") or {}
        img_url = orig.get("url")
        if img_url:
            return {"type": "image", "url": img_url, "title": title}

        return None
    except Exception as e:
        logger.error(f"Pinterest image API error: {e}")
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
        "📸 إنستقرام — ريلز وفيديوهات\n"
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

    if platform == "tiktok":
        info = await loop.run_in_executor(None, get_tiktok_info, url)
        if not info:
            await update.message.reply_text("❌ تعذّر جلب معلومات الفيديو. تأكد من صحة الرابط.")
            return
        title = info.get("title", "تيك توك")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform, "tiktok_data": info}

    elif platform == "pinterest":
        pin_info = await loop.run_in_executor(None, get_pinterest_info, url)
        if not pin_info:
            await update.message.reply_text("❌ تعذّر جلب المحتوى. تأكد من صحة الرابط.")
            return
        if pin_info["type"] == "video":
            yt_info = pin_info["info"]
            title = yt_info.get("title", "بينترست")
            duration = yt_info.get("duration", 0)
        else:
            title = pin_info.get("title", "بينترست")
            duration = 0
        session_data = {"url": url, "title": title, "platform": platform, "pin_info": pin_info}

    elif platform == "instagram":
        info = await loop.run_in_executor(None, get_video_info, url)
        if not info:
            await update.message.reply_text(
                "⚠️ إنستقرام يتطلب تسجيل الدخول لتحميل المحتوى من الخوادم.\n\n"
                "💡 يمكنك تجربة روابط تيك توك أو يوتيوب أو بينترست."
            )
            return
        title = info.get("title", "إنستقرام")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform}

    else:
        info = await loop.run_in_executor(None, get_video_info, url)
        if not info:
            await update.message.reply_text("❌ تعذّر جلب معلومات الفيديو. تأكد من صحة الرابط.")
            return
        title = info.get("title", "يوتيوب")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform}

    _cleanup_sessions()
    session_id = str(uuid.uuid4())
    user_id = update.message.from_user.id
    session_data.update({"ts": time.time(), "user_id": user_id, "chat_id": update.message.chat_id})
    _SESSIONS[session_id] = session_data

    keyboard = []
    if pinfo["supports_audio"]:
        keyboard.append([
            InlineKeyboardButton("🎥 تحميل فيديو", callback_data=f"video:{session_id}"),
            InlineKeyboardButton("🎵 تحميل MP3", callback_data=f"audio:{session_id}"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("⬇️ تحميل", callback_data=f"video:{session_id}"),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    safe_title = escape_markdown(title)
    safe_platform = escape_markdown(pinfo["name"])
    duration_str = format_duration(duration) if duration else "—"
    safe_duration = escape_markdown(duration_str)

    caption = (
        f"{pinfo['icon']} *{safe_title}*\n"
        f"📡 المنصة: {safe_platform}\n"
        f"⏱ المدة: {safe_duration}\n\n"
        "اختر نوع التحميل:"
    )
    await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode="MarkdownV2")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if ":" not in data:
        await query.edit_message_text("❌ طلب غير صالح.")
        return

    action, session_id = data.split(":", 1)
    session = _SESSIONS.get(session_id)
    if not session:
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return

    if session.get("user_id") and session["user_id"] != query.from_user.id:
        await query.answer("❌ هذا الطلب ليس لك.", show_alert=True)
        return

    if time.time() - session.get("ts", 0) > SESSION_TTL:
        del _SESSIONS[session_id]
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return

    platform = session.get("platform", "youtube")
    url = session["url"]
    title = session["title"]

    if action == "video":
        if platform == "tiktok":
            await _send_tiktok(query, context, session)
        elif platform == "pinterest":
            await _send_pinterest(query, context, session)
        else:
            await _download_video(query, context, url, title, platform)
    elif action == "audio":
        await _download_audio(query, context, url, title)
    else:
        await query.edit_message_text("❌ طلب غير معروف.")


async def _send_tiktok(query, context, session: dict) -> None:
    pinfo = PLATFORM_INFO["tiktok"]
    title = session["title"]
    tk_data = session.get("tiktok_data", {})
    await query.edit_message_text("⏳ جاري تحميل فيديو تيك توك بدون علامة مائية...")

    video_url = tk_data.get("hdplay") or tk_data.get("play")
    if not video_url:
        await query.edit_message_text("❌ تعذّر الحصول على رابط الفيديو.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "tiktok.mp4"
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _download_url_to_file, video_url, str(video_path))
        except Exception as e:
            logger.error(f"TikTok download error: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحميل.")
            return

        file_size = video_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
            )
            return

        try:
            await query.edit_message_text("📤 جاري إرسال الفيديو...")
            with open(video_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=f"{pinfo['icon']} {title}",
                    supports_streaming=True,
                )
            await query.delete_message()
        except Exception as e:
            logger.error(f"TikTok send error: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء إرسال الفيديو.")


async def _send_pinterest(query, context, session: dict) -> None:
    pinfo = PLATFORM_INFO["pinterest"]
    title = session["title"]
    pin_info = session.get("pin_info", {})
    pin_type = pin_info.get("type")

    await query.edit_message_text("⏳ جاري تحميل المحتوى من بينترست...")

    if pin_type == "video":
        url = session["url"]
        await _download_video(query, context, url, title, "pinterest")
        return

    if pin_type == "image":
        img_url = pin_info["url"]
        with tempfile.TemporaryDirectory() as tmpdir:
            img_path = Path(tmpdir) / "pinterest.jpg"
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _download_url_to_file, img_url, str(img_path))
            except Exception as e:
                logger.error(f"Pinterest image download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء تحميل الصورة.")
                return

            file_size = img_path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                await query.edit_message_text(
                    f"⚠️ حجم الصورة ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
                )
                return

            try:
                await query.edit_message_text("📤 جاري إرسال الصورة...")
                with open(img_path, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=f,
                        caption=f"{pinfo['icon']} {title}",
                    )
                await query.delete_message()
            except Exception as e:
                logger.error(f"Pinterest photo send error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الصورة.")
        return

    if pin_type == "video_url":
        video_url = pin_info["url"]
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "pinterest.mp4"
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _download_url_to_file, video_url, str(video_path))
            except Exception as e:
                logger.error(f"Pinterest video download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء تحميل الفيديو.")
                return

            file_size = video_path.stat().st_size
            if file_size > MAX_FILE_SIZE:
                await query.edit_message_text(
                    f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
                )
                return

            try:
                await query.edit_message_text("📤 جاري إرسال الفيديو...")
                with open(video_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption=f"{pinfo['icon']} {title}",
                        supports_streaming=True,
                    )
                await query.delete_message()
            except Exception as e:
                logger.error(f"Pinterest video send error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الفيديو.")
        return

    await query.edit_message_text("❌ تعذّر معالجة هذا المحتوى.")


def _download_url_to_file(url: str, path: str) -> None:
    with httpx.stream(
        "GET", url,
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    ) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 64):
                f.write(chunk)


def _build_video_opts(platform: str, output_template: str) -> dict:
    base = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if platform == "instagram":
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
    await query.edit_message_text(f"⏳ جاري تحميل الفيديو {pinfo['icon']} {pinfo['name']}...")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="upload_video")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = _build_video_opts(platform, output_template)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Video download error ({platform}): {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحميل. حاول مجدداً.")
            return

        all_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
        if not all_files:
            await query.edit_message_text("❌ لم يتم العثور على الملف. حاول مجدداً.")
            return

        mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
        candidates = mp4_files if mp4_files else all_files
        video_file = max(candidates, key=lambda f: f.stat().st_size)
        file_size = video_file.stat().st_size

        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
            )
            return

        try:
            await query.edit_message_text("📤 جاري إرسال الفيديو...")
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
            await query.edit_message_text("❌ حدث خطأ أثناء إرسال الفيديو.")


async def _download_audio(query, context, url: str, title: str) -> None:
    await query.edit_message_text("⏳ جاري تحميل وتحويل الصوت إلى MP3...")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="upload_document")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Audio download error: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء تحميل الصوت. حاول مجدداً.")
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
                f"⚠️ حجم ملف الصوت ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
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
            await query.edit_message_text("❌ حدث خطأ أثناء إرسال ملف الصوت.")


def _run_ydl(ydl_opts: dict, url: str):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.critical(
            "BOT_TOKEN is not set. "
            "Please add it to Replit Secrets: Secrets tab → BOT_TOKEN → your token from @BotFather."
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
