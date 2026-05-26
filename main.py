import os
import logging
import re
import asyncio
import subprocess
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

# ── ثوابت ───────────────────────────────────────────────────────────────────
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.|m\.|music\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"[\w\-]+(\?[\w=&\-%]*)?"
)
TIKTOK_REGEX = re.compile(
    r"https?://(www\.|vm\.|vt\.|m\.)?tiktok\.com/[\w@/\-\?=&\.]+"
    r"|https?://vt\.tiktok\.com/[\w]+"
    r"|https?://vm\.tiktok\.com/[\w]+"
)
INSTAGRAM_REGEX = re.compile(
    r"https?://(www\.)?instagram\.com/(p|reel|tv|stories)/[\w\-]+(/?)(\?[\w=&\-%]*)?"
)
PINTEREST_REGEX = re.compile(
    r"https?://(?:(?:www\.|[a-z]{2}\.)?pinterest\.(?:com|[a-z]{2,3})/pin/[\d]+"
    r"|pin\.it/[\w]+)(\?[\w=&\-%]*)?"
)

MAX_FILE_SIZE = 50 * 1024 * 1024
SESSION_TTL  = 3600
TIKWM_API    = "https://tikwm.com/api/"
PINTEREST_WIDGETS_API = "https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={}"

# sessionid ثابت لتحميل إنستقرام — مشترك لجميع المستخدمين
IG_SESSIONID = "70097632584%3ATQuAgk17CobNm9%3A18%3AAYiMNf-GmQbcVFu0gr15HLSHWVgBK0vaViLsX6viuQ"

_SESSIONS: dict[str, dict] = {}

PLATFORM_INFO = {
    "youtube":   {"name": "يوتيوب",   "icon": "▶️",  "supports_audio": True},
    "tiktok":    {"name": "تيك توك",  "icon": "🎵",  "supports_audio": False},
    "instagram": {"name": "إنستقرام", "icon": "📸",  "supports_audio": False},
    "pinterest": {"name": "بينترست",  "icon": "📌",  "supports_audio": False},
}

# رسائل الانتظار المتناوبة
_WAIT_MSGS = [
    "⏳ جاري التحميل…",
    "📡 يتم جلب الفيديو…",
    "🔄 يتم المعالجة…",
    "🚀 اللمسات الأخيرة…",
    "📦 يتم تجهيز الملف…",
]


# ── مساعدات عامة ─────────────────────────────────────────────────────────────
def _cleanup_sessions() -> None:
    now = time.time()
    expired = [sid for sid, s in _SESSIONS.items() if now - s.get("ts", 0) > SESSION_TTL]
    for sid in expired:
        del _SESSIONS[sid]


def escape_markdown(text: str) -> str:
    return re.sub(r"([\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])", r"\\\1", text)


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


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}ث"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}د {s}ث"
    h, m = divmod(m, 60)
    return f"{h}س {m}د {s}ث"


def _make_ig_cookies_file(sessionid: str) -> str:
    content = (
        "# Netscape HTTP Cookie File\n"
        f".instagram.com\tTRUE\t/\tTRUE\t2999999999\tsessionid\t{sessionid}\n"
        ".instagram.com\tTRUE\t/\tFALSE\t2999999999\tds_user_id\t0\n"
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


async def _spinner(msg, stop_event: asyncio.Event) -> None:
    """يحدّث نص الرسالة كل 4 ثوانٍ لإظهار أن البوت يعمل."""
    idx = 0
    while not stop_event.is_set():
        await asyncio.sleep(4)
        if stop_event.is_set():
            break
        idx = (idx + 1) % len(_WAIT_MSGS)
        try:
            await msg.edit_text(_WAIT_MSGS[idx])
        except Exception:
            pass


# ── استخراج المعلومات ─────────────────────────────────────────────────────────
def get_video_info(url: str, cookies_file: str | None = None) -> dict | None:
    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"get_video_info error: {e}")
        return None


def get_tiktok_info(url: str) -> dict | None:
    try:
        r = httpx.post(
            TIKWM_API,
            data={"url": url},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        d = r.json()
        if d.get("code") == 0 and d.get("data"):
            return d["data"]
    except Exception as e:
        logger.error(f"TikTok API error: {e}")
    return None


def get_pinterest_info(url: str) -> dict | None:
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {"type": "video", "info": info}
    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        if "No video formats found" in err or "no video formats" in err.lower():
            pin_id = _extract_pin_id_from_error(err) or _extract_pinterest_pin_id(url)
            if pin_id:
                return _get_pinterest_image(pin_id)
        logger.error(f"Pinterest yt-dlp error: {e}")
    except Exception as e:
        logger.error(f"Pinterest error: {e}")
    return None


def _extract_pin_id_from_error(err: str) -> str | None:
    m = re.search(r"\[Pinterest\]\s+(\d+):", err)
    return m.group(1) if m else None


def _extract_pinterest_pin_id(url: str) -> str | None:
    m = re.search(r"/pin/(\d+)", url)
    if m:
        return m.group(1)
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r"/pin/(\d+)", str(r.url))
        return m.group(1) if m else None
    except Exception:
        return None


def _get_pinterest_image(pin_id: str) -> dict | None:
    try:
        for attempt in range(3):
            r = httpx.get(
                PINTEREST_WIDGETS_API.format(pin_id),
                headers={"User-Agent": "Mozilla/5.0"},
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
        pin   = pins[0]
        title = pin.get("description") or pin.get("title") or "بينترست"

        story = pin.get("story_pin_data")
        if story:
            for page in story.get("pages", []):
                vid = page.get("video")
                if vid:
                    for key in ("V_720P", "V_480P", "V_360P", "V_EXP4"):
                        vl = vid.get("video_list", {})
                        if key in vl:
                            return {"type": "video_url", "url": vl[key]["url"], "title": title}
                img = page.get("image", {})
                if img:
                    orig = img.get("images", {}).get("originals", {})
                    if orig.get("url"):
                        return {"type": "image", "url": orig["url"], "title": title}

        images = pin.get("images") or {}
        orig   = (images.get("originals") or {})
        if orig.get("url"):
            return {"type": "image", "url": orig["url"], "title": title}
    except Exception as e:
        logger.error(f"Pinterest image API error: {e}")
    return None


# ── أوامر ─────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 أهلاً بك في Snaptube Bot!\n\n"
        "📌 المنصات المدعومة:\n"
        "▶️ يوتيوب — فيديو حتى 1080p أو MP3\n"
        "🎵 تيك توك — بدون علامة مائية\n"
        "📸 إنستقرام — ريلز وفيديوهات\n"
        "📌 بينترست — فيديوهات وصور\n\n"
        "💡 فقط أرسل الرابط وسأتولى الباقي!"
    )


# ── استقبال الروابط ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    platform, url = detect_platform(text)

    if not platform:
        await update.message.reply_text(
            "❌ الرابط غير مدعوم.\n\n"
            "المنصات المدعومة: يوتيوب · تيك توك · إنستقرام · بينترست"
        )
        return

    pinfo    = PLATFORM_INFO[platform]
    user_id  = update.message.from_user.id

    # رسالة واحدة تُعدَّل لاحقاً (لا ترسل رسالتين)
    status_msg = await update.message.reply_text(
        f"⏳ جاري جلب معلومات {pinfo['icon']} {pinfo['name']}…"
    )

    loop = asyncio.get_running_loop()

    # ── تيك توك ──────────────────────────────────────────────────────────────
    if platform == "tiktok":
        info = await loop.run_in_executor(None, get_tiktok_info, url)
        if not info:
            await status_msg.edit_text("❌ تعذّر جلب معلومات الفيديو. تأكد من صحة الرابط.")
            return
        title    = info.get("title", "تيك توك")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform, "tiktok_data": info}

    # ── بينترست ──────────────────────────────────────────────────────────────
    elif platform == "pinterest":
        pin_info = await loop.run_in_executor(None, get_pinterest_info, url)
        if not pin_info:
            await status_msg.edit_text("❌ تعذّر جلب المحتوى. تأكد من صحة الرابط.")
            return
        if pin_info["type"] == "video":
            title    = pin_info["info"].get("title", "بينترست")
            duration = pin_info["info"].get("duration", 0)
        else:
            title    = pin_info.get("title", "بينترست")
            duration = 0
        session_data = {"url": url, "title": title, "platform": platform, "pin_info": pin_info}

    # ── إنستقرام: عرض الزر فوراً بدون انتظار استخراج المعلومات ─────────────
    elif platform == "instagram":
        title    = "إنستقرام"
        duration = 0
        session_data = {"url": url, "title": title, "platform": platform}

    # ── يوتيوب ───────────────────────────────────────────────────────────────
    else:
        info = await loop.run_in_executor(None, get_video_info, url)
        if not info:
            await status_msg.edit_text("❌ تعذّر جلب معلومات الفيديو. تأكد من صحة الرابط.")
            return
        title    = info.get("title", "يوتيوب")
        duration = info.get("duration", 0)
        session_data = {"url": url, "title": title, "platform": platform}

    # ── بناء لوحة الأزرار ────────────────────────────────────────────────────
    _cleanup_sessions()
    session_id = str(uuid.uuid4())
    session_data.update({"ts": time.time(), "user_id": user_id, "chat_id": update.message.chat_id})
    _SESSIONS[session_id] = session_data

    if pinfo["supports_audio"]:
        keyboard = [[
            InlineKeyboardButton("🎥 فيديو", callback_data=f"video:{session_id}"),
            InlineKeyboardButton("🎵 MP3",   callback_data=f"audio:{session_id}"),
        ]]
    else:
        keyboard = [[InlineKeyboardButton("⬇️ تحميل", callback_data=f"video:{session_id}")]]

    duration_str = format_duration(duration) if duration else "—"
    caption = (
        f"{pinfo['icon']} *{escape_markdown(title)}*\n"
        f"📡 المنصة: {escape_markdown(pinfo['name'])}\n"
        f"⏱ المدة: {escape_markdown(duration_str)}\n\n"
        "اختر نوع التحميل:"
    )
    # تعديل رسالة الانتظار الأولى بدل إرسال رسالة جديدة ← لا تكرار
    await status_msg.edit_text(
        caption,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2",
    )


# ── معالج الأزرار ─────────────────────────────────────────────────────────────
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
    url      = session["url"]
    title    = session["title"]

    if action == "video":
        if platform == "tiktok":
            await _send_tiktok(query, context, session)
        elif platform == "pinterest":
            await _send_pinterest(query, context, session)
        elif platform == "instagram":
            await _download_video(query, context, url, title, "instagram")
        else:
            await _download_video(query, context, url, title, platform)
    elif action == "audio":
        await _download_audio(query, context, url, title)
    else:
        await query.edit_message_text("❌ طلب غير معروف.")


# ── إعادة ترميز H.264 ─────────────────────────────────────────────────────────
def _reencode_h264(input_path: str, output_path: str) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ],
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg: {result.stderr.decode()[:300]}")


# ── تيك توك ───────────────────────────────────────────────────────────────────
async def _send_tiktok(query, context, session: dict) -> None:
    pinfo   = PLATFORM_INFO["tiktok"]
    title   = session["title"]
    tk_data = session.get("tiktok_data", {})

    video_url = tk_data.get("play")
    if not video_url:
        await query.edit_message_text("❌ تعذّر الحصول على رابط الفيديو.")
        return

    stop = asyncio.Event()
    await query.edit_message_text(_WAIT_MSGS[0])
    spin_task = asyncio.create_task(_spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path   = Path(tmpdir) / "tiktok_raw.mp4"
            final_path = Path(tmpdir) / "tiktok.mp4"

            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, _download_url_to_file, video_url, str(raw_path))
            except Exception as e:
                logger.error(f"TikTok download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء التحميل.")
                return

            if raw_path.stat().st_size > MAX_FILE_SIZE:
                await query.edit_message_text(
                    f"⚠️ حجم الفيديو يتجاوز 50 ميجابايت."
                )
                return

            try:
                await loop.run_in_executor(None, _reencode_h264, str(raw_path), str(final_path))
                send_path = final_path if final_path.exists() and final_path.stat().st_size > 0 else raw_path
            except Exception as e:
                logger.warning(f"Re-encode failed, using raw: {e}")
                send_path = raw_path

            stop.set()
            await query.edit_message_text("📤 جاري إرسال الفيديو…")
            try:
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
    finally:
        stop.set()
        spin_task.cancel()


# ── إنستقرام ──────────────────────────────────────────────────────────────────
async def _download_video(query, context, url: str, title: str, platform: str) -> None:
    pinfo = PLATFORM_INFO.get(platform, PLATFORM_INFO["youtube"])

    stop = asyncio.Event()
    await query.edit_message_text(_WAIT_MSGS[0])
    spin_task = asyncio.create_task(_spinner(query.message, stop))

    cookies_file: str | None = None
    if platform == "instagram":
        cookies_file = _make_ig_cookies_file(IG_SESSIONID)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
            ydl_opts = _build_video_opts(platform, output_template, cookies_file)

            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
            except Exception as e:
                logger.error(f"Video download error ({platform}): {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء التحميل. حاول مجدداً.")
                return
            finally:
                if cookies_file:
                    try:
                        os.unlink(cookies_file)
                        cookies_file = None
                    except Exception:
                        pass

            all_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
            if not all_files:
                await query.edit_message_text("❌ لم يتم العثور على الملف. حاول مجدداً.")
                return

            mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
            candidates = mp4_files if mp4_files else all_files
            video_file = max(candidates, key=lambda f: f.stat().st_size)

            # إنستقرام: إعادة ترميز H.264 لضمان ظهور الصورة في تيليجرام
            if platform == "instagram":
                final_path = Path(tmpdir) / "ig_final.mp4"
                try:
                    await loop.run_in_executor(
                        None, _reencode_h264, str(video_file), str(final_path)
                    )
                    if final_path.exists() and final_path.stat().st_size > 0:
                        video_file = final_path
                except Exception as e:
                    logger.warning(f"Instagram re-encode failed, using raw: {e}")

            file_size = video_file.stat().st_size
            if file_size > MAX_FILE_SIZE:
                await query.edit_message_text(
                    f"⚠️ حجم الفيديو ({file_size // (1024*1024)} ميجابايت) يتجاوز الحد."
                )
                return

            stop.set()
            await query.edit_message_text("📤 جاري إرسال الفيديو…")
            try:
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
    finally:
        stop.set()
        spin_task.cancel()
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass


# ── بينترست ───────────────────────────────────────────────────────────────────
async def _send_pinterest(query, context, session: dict) -> None:
    pinfo    = PLATFORM_INFO["pinterest"]
    title    = session["title"]
    pin_info = session.get("pin_info", {})
    pin_type = pin_info.get("type")

    stop = asyncio.Event()
    await query.edit_message_text(_WAIT_MSGS[0])
    spin_task = asyncio.create_task(_spinner(query.message, stop))

    try:
        if pin_type == "video":
            stop.set()
            await _download_video(query, context, session["url"], title, "pinterest")
            return

        if pin_type in ("image", "video_url"):
            media_url = pin_info["url"]
            suffix    = ".mp4" if pin_type == "video_url" else ".jpg"
            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"pinterest{suffix}"
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, _download_url_to_file, media_url, str(media_path))
                except Exception as e:
                    logger.error(f"Pinterest media download error: {e}")
                    await query.edit_message_text("❌ حدث خطأ أثناء التحميل.")
                    return

                if media_path.stat().st_size > MAX_FILE_SIZE:
                    await query.edit_message_text("⚠️ الملف يتجاوز حد 50 ميجابايت.")
                    return

                stop.set()
                await query.edit_message_text("📤 جاري الإرسال…")
                try:
                    with open(media_path, "rb") as f:
                        if pin_type == "image":
                            await context.bot.send_photo(
                                chat_id=query.message.chat_id,
                                photo=f,
                                caption=f"{pinfo['icon']} {title}",
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=f,
                                caption=f"{pinfo['icon']} {title}",
                                supports_streaming=True,
                            )
                    await query.delete_message()
                except Exception as e:
                    logger.error(f"Pinterest send error: {e}")
                    await query.edit_message_text("❌ حدث خطأ أثناء الإرسال.")
            return

        await query.edit_message_text("❌ تعذّر معالجة هذا المحتوى.")
    finally:
        stop.set()
        spin_task.cancel()


# ── يوتيوب صوت ────────────────────────────────────────────────────────────────
async def _download_audio(query, context, url: str, title: str) -> None:
    stop = asyncio.Event()
    await query.edit_message_text(_WAIT_MSGS[0])
    spin_task = asyncio.create_task(_spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
            except Exception as e:
                logger.error(f"Audio download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء التحميل. حاول مجدداً.")
                return

            all_files = [f for f in Path(tmpdir).glob("*") if f.is_file()]
            if not all_files:
                await query.edit_message_text("❌ لم يتم العثور على الملف.")
                return

            mp3_files = [f for f in all_files if f.suffix.lower() == ".mp3"]
            audio_file = max(mp3_files or all_files, key=lambda f: f.stat().st_mtime)

            if audio_file.stat().st_size > MAX_FILE_SIZE:
                await query.edit_message_text("⚠️ الملف يتجاوز حد 50 ميجابايت.")
                return

            stop.set()
            await query.edit_message_text("📤 جاري إرسال ملف الصوت…")
            try:
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
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الملف.")
    finally:
        stop.set()
        spin_task.cancel()


# ── مساعدات تحميل ─────────────────────────────────────────────────────────────
def _download_url_to_file(url: str, path: str) -> None:
    with httpx.stream(
        "GET", url,
        follow_redirects=True,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    ) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
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

    if platform in ("instagram", "pinterest"):
        base["format"] = "bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best"
    else:
        base["format"] = (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
            "/bestvideo[height<=1080]+bestaudio"
            "/best[height<=1080]/best"
        )
    return base


def _run_ydl(ydl_opts: dict, url: str) -> None:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


# ── تشغيل البوت ──────────────────────────────────────────────────────────────
def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in Replit Secrets!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    domain = (
        (os.environ.get("REPLIT_DOMAINS", "").split(",")[0].strip() or os.environ.get("REPLIT_DEV_DOMAIN", ""))
        if is_production
        else os.environ.get("REPLIT_DEV_DOMAIN", "")
    )

    if domain:
        webhook_path = f"/webhook/{token}"
        webhook_url  = f"https://{domain}{webhook_path}"
        logger.info(f"{'Production' if is_production else 'Development'} webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=8080,
            url_path=webhook_path,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Fallback: polling mode")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
