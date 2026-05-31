import os
import json
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
        "👋 أهلاً بك!\n\n"
        "📥 *تحميل من المنصات:*\n"
        "▶️ يوتيوب — فيديو 1080p أو MP3\n"
        "🎵 تيك توك — بدون علامة مائية\n"
        "📸 إنستقرام — ريلز وفيديوهات\n"
        "📌 بينترست — فيديوهات وصور\n\n"
        "🔄 *تحويل الوسائط:*\n"
        "🎙 أرسل بصمة ← تحويل إلى MP3\n"
        "🎵 أرسل MP3 ← تحويل إلى بصمة\n"
        "🖼 أرسل صورة ← ملصق + PNG 512×512\n"
        "🏷 أرسل ملصق ← تحويل إلى صورة\n"
        "📹 أرسل فيديو ← صوت + ملصق متحرك\n"
        "⭕ أرسل فيديو دائري ← فيديو + بصمة\n"
        "📄 أرسل ملف (Word/Excel/...) ← PDF\n\n"
        "💡 أرسل الرابط أو الملف وسأتولى الباقي!"
    )


# ── تحميل فوري: تيك توك ───────────────────────────────────────────────────────
async def _inline_tiktok(update: Update, context, url: str,
                          status_msg, stop: asyncio.Event, loop) -> None:
    pinfo    = PLATFORM_INFO["tiktok"]
    chat_id  = update.message.chat_id

    info = await loop.run_in_executor(None, get_tiktok_info, url)
    if not info:
        await status_msg.edit_text("❌ تعذّر جلب الفيديو. تأكد من الرابط.")
        return

    title     = info.get("title", "تيك توك")
    video_url = info.get("play") or ""
    if not video_url:
        await status_msg.edit_text("❌ لم يتم العثور على رابط الفيديو.")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_path   = Path(tmpdir) / "raw.mp4"
        final_path = Path(tmpdir) / "final.mp4"

        try:
            await loop.run_in_executor(None, _download_url_to_file, video_url, str(raw_path))
        except Exception as e:
            logger.error(f"TikTok inline download error: {e}")
            await status_msg.edit_text("❌ حدث خطأ أثناء التحميل.")
            return

        if raw_path.stat().st_size > MAX_FILE_SIZE:
            await status_msg.edit_text("⚠️ حجم الفيديو يتجاوز 50 ميجابايت.")
            return

        try:
            await loop.run_in_executor(None, _reencode_h264, str(raw_path), str(final_path))
            send_path = final_path if final_path.exists() and final_path.stat().st_size > 0 else raw_path
        except Exception as e:
            logger.warning(f"Re-encode failed: {e}")
            send_path = raw_path

        stop.set()
        await status_msg.edit_text("📤 جاري الإرسال…")
        try:
            with open(send_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id, video=f,
                    caption=f"{pinfo['icon']} {title}",
                    supports_streaming=True,
                )
            await status_msg.delete()
            await _send_extracted_audio(context, chat_id, str(send_path), title, pinfo["icon"], loop)
        except Exception as e:
            logger.error(f"TikTok inline send error: {e}")
            await status_msg.edit_text("❌ حدث خطأ أثناء الإرسال.")


# ── تحميل فوري: yt-dlp (إنستقرام وبينترست فيديو) ─────────────────────────────
async def _inline_ydl(update: Update, context, url: str, platform: str,
                       status_msg, stop: asyncio.Event, loop) -> None:
    pinfo        = PLATFORM_INFO[platform]
    chat_id      = update.message.chat_id
    cookies_file = None
    if platform == "instagram":
        cookies_file = _make_ig_cookies_file(IG_SESSIONID)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_tmpl = str(Path(tmpdir) / "%(title)s.%(ext)s")
            opts     = _build_video_opts(platform, out_tmpl, cookies_file)
            opts["writeinfojson"] = True

            try:
                await loop.run_in_executor(None, lambda: _run_ydl(opts, url))
            except Exception as e:
                logger.error(f"Inline ydl error ({platform}): {e}")
                await status_msg.edit_text("❌ حدث خطأ أثناء التحميل.")
                return
            finally:
                if cookies_file:
                    try:
                        os.unlink(cookies_file)
                        cookies_file = None
                    except Exception:
                        pass

            # العنوان من ملف JSON
            title = pinfo["name"]
            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files = [f for f in Path(tmpdir).glob("*")
                         if f.is_file() and f.suffix.lower() != ".json"]
            if not all_files:
                await status_msg.edit_text("❌ لم يتم العثور على الملف.")
                return

            mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
            video_file = max(mp4_files or all_files, key=lambda f: f.stat().st_size)

            if platform == "instagram":
                final_path = Path(tmpdir) / "ig_final.mp4"
                try:
                    await loop.run_in_executor(None, _reencode_h264, str(video_file), str(final_path))
                    if final_path.exists() and final_path.stat().st_size > 0:
                        video_file = final_path
                except Exception as e:
                    logger.warning(f"IG re-encode failed: {e}")

            if video_file.stat().st_size > MAX_FILE_SIZE:
                await status_msg.edit_text(
                    f"⚠️ حجم الفيديو ({video_file.stat().st_size // (1024*1024)} MB) يتجاوز الحد."
                )
                return

            stop.set()
            await status_msg.edit_text("📤 جاري الإرسال…")
            with open(video_file, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id, video=f,
                    caption=f"{pinfo['icon']} {title}",
                    supports_streaming=True,
                )
            await status_msg.delete()
            await _send_extracted_audio(context, chat_id, str(video_file), title, pinfo["icon"], loop)
    finally:
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass


# ── تحميل فوري: بينترست ───────────────────────────────────────────────────────
async def _inline_pinterest(update: Update, context, url: str,
                             status_msg, stop: asyncio.Event, loop) -> None:
    pinfo   = PLATFORM_INFO["pinterest"]
    chat_id = update.message.chat_id

    pin_info = await loop.run_in_executor(None, get_pinterest_info, url)
    if not pin_info:
        await status_msg.edit_text("❌ تعذّر جلب المحتوى.")
        return

    pin_type = pin_info.get("type")

    if pin_type == "video":
        title = pin_info["info"].get("title", "بينترست")
        await _inline_ydl(update, context, url, "pinterest", status_msg, stop, loop)
        return

    if pin_type in ("image", "video_url"):
        title     = pin_info.get("title", "بينترست")
        media_url = pin_info["url"]
        suffix    = ".mp4" if pin_type == "video_url" else ".jpg"

        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = Path(tmpdir) / f"pin{suffix}"
            try:
                await loop.run_in_executor(None, _download_url_to_file, media_url, str(media_path))
            except Exception as e:
                logger.error(f"Pinterest inline download error: {e}")
                await status_msg.edit_text("❌ حدث خطأ أثناء التحميل.")
                return

            if media_path.stat().st_size > MAX_FILE_SIZE:
                await status_msg.edit_text("⚠️ الملف يتجاوز 50 ميجابايت.")
                return

            stop.set()
            await status_msg.edit_text("📤 جاري الإرسال…")
            with open(media_path, "rb") as f:
                if pin_type == "image":
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=f,
                        caption=f"{pinfo['icon']} {title}",
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id, video=f,
                        caption=f"{pinfo['icon']} {title}",
                        supports_streaming=True,
                    )
            await status_msg.delete()
            if pin_type == "video_url":
                await _send_extracted_audio(
                    context, chat_id, str(media_path), title, pinfo["icon"], loop
                )
        return

    await status_msg.edit_text("❌ تعذّر معالجة هذا المحتوى.")


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

    loop = asyncio.get_running_loop()

    # يوتيوب: عرض زرّي الاختيار فوراً بلا جلب معلومات
    if platform == "youtube":
        _cleanup_sessions()
        sid = str(uuid.uuid4())
        _SESSIONS[sid] = {
            "url": url, "title": "يوتيوب", "platform": "youtube",
            "ts": time.time(),
            "user_id": update.message.from_user.id,
            "chat_id": update.message.chat_id,
        }
        keyboard = [[
            InlineKeyboardButton("🎥 فيديو", callback_data=f"video:{sid}"),
            InlineKeyboardButton("🎵 MP3",   callback_data=f"audio:{sid}"),
        ]]
        await update.message.reply_text(
            "▶️ يوتيوب — اختر نوع التحميل:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # باقي المنصات: تحميل فوري بدون أزرار
    status_msg = await update.message.reply_text("⏳ جاري التحميل…")
    stop       = asyncio.Event()
    spin_task  = asyncio.create_task(_spinner(status_msg, stop))
    try:
        if platform == "tiktok":
            await _inline_tiktok(update, context, url, status_msg, stop, loop)
        elif platform == "instagram":
            await _inline_ydl(update, context, url, "instagram", status_msg, stop, loop)
        elif platform == "pinterest":
            await _inline_pinterest(update, context, url, status_msg, stop, loop)
    finally:
        stop.set()
        spin_task.cancel()


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


def _extract_mp3(video_path: str, output_path: str) -> None:
    """استخراج صوت MP3 من ملف الفيديو."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn",
            "-c:a", "libmp3lame", "-q:a", "2",
            output_path,
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio: {result.stderr.decode()[:200]}")


async def _send_extracted_audio(context, chat_id: int, video_path: str,
                                 title: str, icon: str, loop) -> None:
    """يستخرج MP3 من الفيديو ويرسله، ولا يوقف العملية عند الخطأ."""
    try:
        audio_path = str(video_path).rsplit(".", 1)[0] + "_audio.mp3"
        await loop.run_in_executor(None, _extract_mp3, str(video_path), audio_path)
        audio_file = Path(audio_path)
        if not audio_file.exists() or audio_file.stat().st_size == 0:
            return
        if audio_file.stat().st_size > MAX_FILE_SIZE:
            return
        with open(audio_path, "rb") as f:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=f,
                title=title,
                caption=f"🎵 {title}",
            )
    except Exception as e:
        logger.warning(f"Auto audio extraction failed (non-critical): {e}")


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
                await _send_extracted_audio(
                    context, query.message.chat_id, str(send_path), title, pinfo["icon"], loop
                )
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
            ydl_opts["writeinfojson"] = True

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

            # العنوان الحقيقي من ملف JSON
            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files = [f for f in Path(tmpdir).glob("*")
                         if f.is_file() and f.suffix.lower() != ".json"]
            if not all_files:
                await query.edit_message_text("❌ لم يتم العثور على الملف. حاول مجدداً.")
                return

            mp4_files = [f for f in all_files if f.suffix.lower() == ".mp4"]
            video_file = max(mp4_files or all_files, key=lambda f: f.stat().st_size)

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
                await _send_extracted_audio(
                    context, query.message.chat_id, str(video_file), title, pinfo["icon"], loop
                )
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
                    if pin_type == "video_url":
                        await _send_extracted_audio(
                            context, query.message.chat_id, str(media_path),
                            title, pinfo["icon"], loop
                        )
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
                "writeinfojson": True,
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

            # العنوان الحقيقي من ملف JSON
            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files = [f for f in Path(tmpdir).glob("*")
                         if f.is_file() and f.suffix.lower() != ".json"]
            if not all_files:
                await query.edit_message_text("❌ لم يتم العثور على الملف.")
                return

            mp3_files  = [f for f in all_files if f.suffix.lower() == ".mp3"]
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


# ── محوّلات الوسائط ───────────────────────────────────────────────────────────

SOFFICE = os.path.expanduser("~/.nix-profile/bin/soffice")

PDF_SUPPORTED_MIME = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/html", "application/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "text/csv",
}
PDF_SUPPORTED_EXT = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".html", ".htm", ".rtf", ".odt", ".ods", ".odp", ".csv",
}


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بصمة (voice) → MP3"""
    msg = await update.message.reply_text("🔄 جاري التحويل إلى MP3…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            vf = await update.message.voice.get_file()
            ogg_path = Path(tmpdir) / "voice.oga"
            await vf.download_to_drive(str(ogg_path))

            mp3_path = Path(tmpdir) / "voice.mp3"
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(ogg_path), "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not mp3_path.exists():
                await msg.edit_text("❌ فشل التحويل إلى MP3.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(mp3_path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=update.message.chat_id, audio=f,
                    title="صوت", caption="🎵 ملف MP3",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_voice error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_audio_to_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """MP3 / صوت → بصمة"""
    msg = await update.message.reply_text("🔄 جاري التحويل إلى بصمة…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_obj = update.message.audio
            af = await audio_obj.get_file()
            ext = Path(audio_obj.file_name or "audio.mp3").suffix or ".mp3"
            dl_path = Path(tmpdir) / f"audio{ext}"
            await af.download_to_drive(str(dl_path))

            ogg_path = Path(tmpdir) / "voice.ogg"
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(dl_path),
                    "-c:a", "libopus", "-b:a", "64k",
                    "-vbr", "on", "-compression_level", "10",
                    str(ogg_path),
                ],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not ogg_path.exists():
                await msg.edit_text("❌ فشل التحويل إلى بصمة.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(ogg_path, "rb") as f:
                await context.bot.send_voice(
                    chat_id=update.message.chat_id, voice=f,
                    caption="🎙 بصمة صوتية",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_audio_to_voice error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_photo_to_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """صورة → ملصق WebP + PNG 512×512"""
    msg = await update.message.reply_text("🔄 جاري تحويل الصورة إلى ملصق…")
    try:
        from PIL import Image as PILImage
        with tempfile.TemporaryDirectory() as tmpdir:
            photo = update.message.photo[-1]
            pf = await photo.get_file()
            src = Path(tmpdir) / "photo.jpg"
            await pf.download_to_drive(str(src))

            img = PILImage.open(src).convert("RGBA")
            img.thumbnail((512, 512), PILImage.LANCZOS)
            canvas = PILImage.new("RGBA", (512, 512), (0, 0, 0, 0))
            x = (512 - img.width) // 2
            y = (512 - img.height) // 2
            canvas.paste(img, (x, y))

            webp_path = Path(tmpdir) / "sticker.webp"
            png_path  = Path(tmpdir) / "sticker_512.png"
            canvas.save(str(webp_path), "WebP", lossless=True)
            canvas.save(str(png_path), "PNG")

            await msg.edit_text("📤 جاري الإرسال…")
            with open(webp_path, "rb") as f:
                await context.bot.send_sticker(chat_id=update.message.chat_id, sticker=f)
            with open(png_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id, document=f,
                    filename="sticker_512.png", caption="🖼 PNG 512×512",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_photo_to_sticker error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_sticker_to_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ملصق → صورة"""
    msg = await update.message.reply_text("🔄 جاري تحويل الملصق إلى صورة…")
    try:
        sticker = update.message.sticker
        sf = await sticker.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            if sticker.is_animated:
                await msg.edit_text("⚠️ الملصقات المتحركة (TGS/Lottie) غير مدعومة حالياً.")
                return
            elif sticker.is_video:
                webm_path = Path(tmpdir) / "sticker.webm"
                await sf.download_to_drive(str(webm_path))
                jpg_path = Path(tmpdir) / "sticker.jpg"
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(webm_path), "-vframes", "1", str(jpg_path)],
                    capture_output=True, timeout=30,
                )
                if r.returncode != 0 or not jpg_path.exists():
                    await msg.edit_text("❌ فشل تحويل الملصق المتحرك.")
                    return
                send_path = jpg_path
            else:
                from PIL import Image as PILImage
                webp_path = Path(tmpdir) / "sticker.webp"
                await sf.download_to_drive(str(webp_path))
                jpg_path = Path(tmpdir) / "sticker.jpg"
                PILImage.open(str(webp_path)).convert("RGB").save(str(jpg_path), "JPEG", quality=95)
                send_path = jpg_path

            await msg.edit_text("📤 جاري الإرسال…")
            with open(send_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=update.message.chat_id, photo=f,
                    caption="🖼 صورة من الملصق",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_sticker_to_photo error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_video_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فيديو مرسل → صوت MP3 + ملصق متحرك WebM"""
    msg = await update.message.reply_text("⏳ جاري معالجة الفيديو…")
    try:
        video = update.message.video
        vf = await video.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            vid_path = Path(tmpdir) / "video.mp4"
            await vf.download_to_drive(str(vid_path))

            await msg.edit_text("🎵 جاري استخراج الصوت…")
            mp3_path = Path(tmpdir) / "audio.mp3"
            r1 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(vid_path), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                capture_output=True, timeout=120,
            )

            await msg.edit_text("🎬 جاري تحويل إلى ملصق متحرك…")
            webm_path = Path(tmpdir) / "sticker.webm"
            r2 = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(vid_path), "-t", "8",
                    "-vf",
                    "scale=512:512:force_original_aspect_ratio=decrease,"
                    "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0",
                    "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "40",
                    "-an", "-r", "24",
                    str(webm_path),
                ],
                capture_output=True, timeout=120,
            )

            await msg.edit_text("📤 جاري الإرسال…")
            sent = False

            if r1.returncode == 0 and mp3_path.exists() and mp3_path.stat().st_size > 0:
                if mp3_path.stat().st_size <= MAX_FILE_SIZE:
                    with open(mp3_path, "rb") as f:
                        await context.bot.send_audio(
                            chat_id=update.message.chat_id, audio=f,
                            title=video.file_name or "صوت",
                            caption="🎵 الصوت المستخرج",
                        )
                    sent = True

            if r2.returncode == 0 and webm_path.exists() and webm_path.stat().st_size > 0:
                try:
                    with open(webm_path, "rb") as f:
                        await context.bot.send_sticker(
                            chat_id=update.message.chat_id, sticker=f,
                        )
                except Exception:
                    with open(webm_path, "rb") as f:
                        await context.bot.send_animation(
                            chat_id=update.message.chat_id, animation=f,
                            caption="🎬 ملصق متحرك",
                        )
                sent = True

            if sent:
                await msg.delete()
            else:
                await msg.edit_text("❌ فشل تحويل الفيديو.")
    except Exception as e:
        logger.error(f"handle_video_convert error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء تحويل الفيديو.")


async def handle_video_note_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فيديو نوت (دائرة) → فيديو عادي + بصمة"""
    msg = await update.message.reply_text("🔄 جاري معالجة الفيديو الدائري…")
    try:
        vn = update.message.video_note
        vf = await vn.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / "vidnote.mp4"
            await vf.download_to_drive(str(src_path))

            await msg.edit_text("🎬 جاري التحويل إلى فيديو عادي…")
            mp4_path = Path(tmpdir) / "video.mp4"
            r1 = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(src_path),
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(mp4_path),
                ],
                capture_output=True, timeout=60,
            )

            ogg_path = Path(tmpdir) / "voice.ogg"
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src_path), "-vn",
                 "-c:a", "libopus", "-b:a", "64k", str(ogg_path)],
                capture_output=True, timeout=60,
            )

            await msg.edit_text("📤 جاري الإرسال…")
            sent = False

            if r1.returncode == 0 and mp4_path.exists() and mp4_path.stat().st_size <= MAX_FILE_SIZE:
                with open(mp4_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=update.message.chat_id, video=f,
                        supports_streaming=True, caption="📹 فيديو عادي",
                    )
                sent = True

            if r2.returncode == 0 and ogg_path.exists() and ogg_path.stat().st_size > 0:
                with open(ogg_path, "rb") as f:
                    await context.bot.send_voice(
                        chat_id=update.message.chat_id, voice=f,
                        caption="🎙 البصمة الصوتية",
                    )
                sent = True

            if sent:
                await msg.delete()
            else:
                await msg.edit_text("❌ فشل تحويل الفيديو الدائري.")
    except Exception as e:
        logger.error(f"handle_video_note_convert error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_document_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ملف (ورد، إكسل، …) → PDF"""
    doc = update.message.document
    if not doc:
        return
    mime = doc.mime_type or ""
    fname = doc.file_name or "file"
    ext = Path(fname).suffix.lower()

    if mime == "application/pdf" or ext == ".pdf":
        await update.message.reply_text("ℹ️ الملف بالفعل بصيغة PDF.")
        return

    if mime not in PDF_SUPPORTED_MIME and ext not in PDF_SUPPORTED_EXT:
        await update.message.reply_text(
            "⚠️ نوع الملف غير مدعوم للتحويل إلى PDF.\n"
            "الأنواع المدعومة: Word · Excel · PowerPoint · TXT · HTML · RTF · ODF"
        )
        return

    msg = await update.message.reply_text("🔄 جاري تحويل الملف إلى PDF…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            df = await doc.get_file()
            dl_path = Path(tmpdir) / fname
            await df.download_to_drive(str(dl_path))

            r = subprocess.run(
                [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, str(dl_path)],
                capture_output=True, timeout=120,
                env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
            )
            if r.returncode != 0:
                logger.error(f"soffice error: {r.stderr.decode()[:300]}")
                await msg.edit_text("❌ فشل تحويل الملف إلى PDF.")
                return

            pdf_name = Path(fname).stem + ".pdf"
            pdf_path = Path(tmpdir) / pdf_name
            if not pdf_path.exists():
                pdfs = list(Path(tmpdir).glob("*.pdf"))
                if pdfs:
                    pdf_path = pdfs[0]
                    pdf_name = pdf_path.name
                else:
                    await msg.edit_text("❌ لم يتم إنشاء ملف PDF.")
                    return

            if pdf_path.stat().st_size > MAX_FILE_SIZE:
                await msg.edit_text("⚠️ حجم PDF يتجاوز حد 50 ميجابايت.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id, document=f,
                    filename=pdf_name, caption=f"📄 {pdf_name}",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_document_to_pdf error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء تحويل الملف.")


# ── تشغيل البوت ──────────────────────────────────────────────────────────────
def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in Replit Secrets!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio_to_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_to_sticker))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker_to_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_convert))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note_convert))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_to_pdf))

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
