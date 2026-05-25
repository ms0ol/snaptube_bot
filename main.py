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
_IG_COOKIES: dict[int, str] = {}  # user_id -> sessionid cookie

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


def _make_ig_cookies_file(sessionid: str) -> str:
    """Write an Instagram sessionid to a Netscape cookies file and return its path."""
    content = (
        "# Netscape HTTP Cookie File\n"
        ".instagram.com\tTRUE\t/\tTRUE\t2999999999\tsessionid\t" + sessionid + "\n"
        ".instagram.com\tTRUE\t/\tFALSE\t2999999999\tds_user_id\t0\n"
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


def get_video_info(url: str, cookies_file: str | None = None) -> dict | None:
    ydl_opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
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
            data={"url": url},
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
        "📸 إنستقرام — ريلز وفيديوهات (يحتاج ربط حساب)\n"
        "📌 بينترست — فيديوهات وصور\n\n"
        "📸 لتفعيل إنستقرام:\n"
        "أرسل /setcookie ثم sessionid الخاص بك\n\n"
        "💡 فقط أرسل الرابط وسأتولى الباقي!"
    )
    await update.message.reply_text(welcome)


async def setcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setcookie command for Instagram session."""
    args = context.args
    user_id = update.message.from_user.id

    if not args:
        await update.message.reply_text(
            "📸 *ربط حساب إنستقرام*\n\n"
            "للتحميل من إنستقرام تحتاج إلى توفير `sessionid` من متصفحك\\.\n\n"
            "*خطوات الحصول على sessionid:*\n"
            "1\\. افتح instagram\\.com في المتصفح\n"
            "2\\. اضغط F12 \\(أدوات المطور\\)\n"
            "3\\. اذهب إلى Application ← Cookies\n"
            "4\\. ابحث عن `sessionid` وانسخ قيمته\n\n"
            "ثم أرسل:\n"
            "`/setcookie قيمة_sessionid`",
            parse_mode="MarkdownV2"
        )
        return

    sessionid = args[0].strip()
    if len(sessionid) < 20:
        await update.message.reply_text("❌ قيمة sessionid غير صحيحة. تأكد من نسخها كاملة.")
        return

    _IG_COOKIES[user_id] = sessionid
    await update.message.reply_text(
        "✅ تم ربط حساب إنستقرام بنجاح!\n"
        "يمكنك الآن إرسال روابط إنستقرام للتحميل."
    )
    logger.info(f"User {user_id} set Instagram cookie (sessionid length: {len(sessionid)})")


async def removecookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removecookie command."""
    user_id = update.message.from_user.id
    if user_id in _IG_COOKIES:
        del _IG_COOKIES[user_id]
        await update.message.reply_text("✅ تم حذف بيانات حساب إنستقرام.")
    else:
        await update.message.reply_text("ℹ️ لا يوجد حساب إنستقرام مرتبط.")


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
    user_id = update.message.from_user.id
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
        sessionid = _IG_COOKIES.get(user_id)
        if not sessionid:
            await update.message.reply_text(
                "📸 إنستقرام يتطلب ربط حسابك أولاً.\n\n"
                "أرسل /setcookie للاطلاع على الطريقة."
            )
            return

        cookies_file = await loop.run_in_executor(None, _make_ig_cookies_file, sessionid)
        try:
            info = await loop.run_in_executor(None, get_video_info, url, cookies_file)
        finally:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass

        if not info:
            await update.message.reply_text(
                "❌ تعذّر جلب الفيديو من إنستقرام.\n\n"
                "تأكد من صحة sessionid وأن الرابط عام.\n"
                "أرسل /setcookie لتحديث بياناتك."
            )
            return
        title = info.get("title", "إنستقرام")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform, "ig_sessionid": sessionid}

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
        elif platform == "instagram":
            await _download_video(query, context, url, title, "instagram",
                                  ig_sessionid=session.get("ig_sessionid"))
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

    # Use `play` (H.264/compatible) NOT `hdplay` (may be H.265 which Telegram can't show inline)
    video_url = tk_data.get("play")
    if not video_url:
        await query.edit_message_text("❌ تعذّر الحصول على رابط الفيديو.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path = Path(tmpdir) / "tiktok_raw.mp4"
        final_path = Path(tmpdir) / "tiktok.mp4"
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _download_url_to_file, video_url, str(raw_path))
        except Exception as e:
            logger.error(f"TikTok download error: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحميل.")
            return

        file_size = raw_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد (50 ميجابايت)."
            )
            return

        # Re-encode to H.264 baseline to guarantee Telegram compatibility
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _reencode_h264, str(raw_path), str(final_path))
            send_path = final_path if final_path.exists() and final_path.stat().st_size > 0 else raw_path
        except Exception as e:
            logger.warning(f"Re-encode failed, using raw: {e}")
            send_path = raw_path

        try:
            await query.edit_message_text("📤 جاري إرسال الفيديو...")
            with open(send_path, "rb") as f:
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


def _reencode_h264(input_path: str, output_path: str) -> None:
    """Re-encode video to H.264 for maximum Telegram compatibility."""
    import subprocess
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path
        ],
        capture_output=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")


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


def _build_video_opts(platform: str, output_template: str, cookies_file: str | None = None) -> dict:
    base: dict = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if cookies_file:
        base["cookiefile"] = cookies_file

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


async def _download_video(query, context, url: str, title: str, platform: str,
                          ig_sessionid: str | None = None) -> None:
    pinfo = PLATFORM_INFO.get(platform, PLATFORM_INFO["youtube"])
    await query.edit_message_text(f"⏳ جاري تحميل الفيديو {pinfo['icon']} {pinfo['name']}...")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action="upload_video")

    cookies_file: str | None = None
    if ig_sessionid:
        cookies_file = _make_ig_cookies_file(ig_sessionid)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
        ydl_opts = _build_video_opts(platform, output_template, cookies_file)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
        except Exception as e:
            logger.error(f"Video download error ({platform}): {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحميل. حاول مجدداً.")
            return
        finally:
            if cookies_file:
                try:
                    os.unlink(cookies_file)
                except Exception:
                    pass

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
    app.add_handler(CommandHandler("setcookie", setcookie_command))
    app.add_handler(CommandHandler("removecookie", removecookie_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"

    if is_production:
        # In production: use webhook so polling doesn't conflict with dev environment
        domain = (
            os.environ.get("REPLIT_DOMAINS", "")
            or os.environ.get("REPLIT_DEV_DOMAIN", "")
        )
        if not domain:
            raise RuntimeError("Cannot determine deployment domain for webhook. Set REPLIT_DOMAINS.")

        # Use first domain if multiple are listed (comma-separated)
        domain = domain.split(",")[0].strip()
        webhook_path = f"/webhook/{token}"
        webhook_url = f"https://{domain}{webhook_path}"

        logger.info(f"Production mode: starting webhook on {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=8080,
            url_path=webhook_path,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Development mode: starting polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
