"""
دوال تحميل الوسائط من جميع المنصات.

الدوال التي تبدأ بـ _inline_* تُستدعى مباشرة عند استقبال الرابط.
الدوال الأخرى (_send_tiktok، _send_pinterest، _download_video، _download_audio)
تُستدعى عند الضغط على الأزرار.
"""

import os
import json
import asyncio
import tempfile
import logging
from pathlib import Path

from telegram import Update

from config import PLATFORM_INFO, MAX_FILE_SIZE, WAIT_MSGS, IG_SESSIONID
from utils import spinner, make_ig_cookies_file
from fetchers import get_tiktok_info, get_pinterest_info
from media_tools import (
    reencode_h264,
    send_extracted_audio,
    download_url_to_file,
    build_video_opts,
    run_ydl,
)

logger = logging.getLogger(__name__)


# ── تيك توك: تحميل فوري (بدون أزرار) ─────────────────────────────────────────
async def inline_tiktok(update: Update, context, url: str,
                        status_msg, stop: asyncio.Event, loop) -> None:
    pinfo   = PLATFORM_INFO["tiktok"]
    chat_id = update.message.chat_id

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
            await loop.run_in_executor(None, download_url_to_file, video_url, str(raw_path))
        except Exception as e:
            logger.error(f"TikTok inline download error: {e}")
            await status_msg.edit_text("❌ حدث خطأ أثناء التحميل.")
            return

        if raw_path.stat().st_size > MAX_FILE_SIZE:
            await status_msg.edit_text("⚠️ حجم الفيديو يتجاوز 50 ميجابايت.")
            return

        try:
            await loop.run_in_executor(None, reencode_h264, str(raw_path), str(final_path))
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
            await send_extracted_audio(context, chat_id, str(send_path), title, pinfo["icon"], loop)
        except Exception as e:
            logger.error(f"TikTok inline send error: {e}")
            await status_msg.edit_text("❌ حدث خطأ أثناء الإرسال.")


# ── yt-dlp: تحميل فوري (إنستقرام وبينترست) ────────────────────────────────────
async def inline_ydl(update: Update, context, url: str, platform: str,
                     status_msg, stop: asyncio.Event, loop) -> None:
    pinfo        = PLATFORM_INFO[platform]
    chat_id      = update.message.chat_id
    cookies_file = None
    if platform == "instagram":
        cookies_file = make_ig_cookies_file()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_tmpl = str(Path(tmpdir) / "%(title)s.%(ext)s")
            opts     = build_video_opts(platform, out_tmpl, cookies_file)
            opts["writeinfojson"] = True

            try:
                await loop.run_in_executor(None, lambda: run_ydl(opts, url))
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

            title = pinfo["name"]
            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files  = [f for f in Path(tmpdir).glob("*")
                          if f.is_file() and f.suffix.lower() != ".json"]
            if not all_files:
                await status_msg.edit_text("❌ لم يتم العثور على الملف.")
                return

            mp4_files  = [f for f in all_files if f.suffix.lower() == ".mp4"]
            video_file = max(mp4_files or all_files, key=lambda f: f.stat().st_size)

            if platform == "instagram":
                final_path = Path(tmpdir) / "ig_final.mp4"
                try:
                    await loop.run_in_executor(None, reencode_h264, str(video_file), str(final_path))
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
            await send_extracted_audio(context, chat_id, str(video_file), title, pinfo["icon"], loop)
    finally:
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass


# ── بينترست: تحميل فوري (بدون أزرار) ─────────────────────────────────────────
async def inline_pinterest(update: Update, context, url: str,
                           status_msg, stop: asyncio.Event, loop) -> None:
    pinfo    = PLATFORM_INFO["pinterest"]
    chat_id  = update.message.chat_id

    pin_info = await loop.run_in_executor(None, get_pinterest_info, url)
    if not pin_info:
        await status_msg.edit_text("❌ تعذّر جلب المحتوى.")
        return

    pin_type = pin_info.get("type")

    if pin_type == "video":
        await inline_ydl(update, context, url, "pinterest", status_msg, stop, loop)
        return

    if pin_type in ("image", "video_url"):
        title     = pin_info.get("title", "بينترست")
        media_url = pin_info["url"]
        suffix    = ".mp4" if pin_type == "video_url" else ".jpg"

        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = Path(tmpdir) / f"pin{suffix}"
            try:
                await loop.run_in_executor(None, download_url_to_file, media_url, str(media_path))
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
                await send_extracted_audio(
                    context, chat_id, str(media_path), title, pinfo["icon"], loop
                )
        return

    await status_msg.edit_text("❌ تعذّر معالجة هذا المحتوى.")


# ── تيك توك: من زر ────────────────────────────────────────────────────────────
async def send_tiktok(query, context, session: dict) -> None:
    pinfo     = PLATFORM_INFO["tiktok"]
    title     = session["title"]
    tk_data   = session.get("tiktok_data", {})
    video_url = tk_data.get("play")

    if not video_url:
        await query.edit_message_text("❌ تعذّر الحصول على رابط الفيديو.")
        return

    stop      = asyncio.Event()
    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path   = Path(tmpdir) / "tiktok_raw.mp4"
            final_path = Path(tmpdir) / "tiktok.mp4"
            loop       = asyncio.get_running_loop()

            try:
                await loop.run_in_executor(None, download_url_to_file, video_url, str(raw_path))
            except Exception as e:
                logger.error(f"TikTok download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء التحميل.")
                return

            if raw_path.stat().st_size > MAX_FILE_SIZE:
                await query.edit_message_text("⚠️ حجم الفيديو يتجاوز 50 ميجابايت.")
                return

            try:
                await loop.run_in_executor(None, reencode_h264, str(raw_path), str(final_path))
                send_path = final_path if final_path.exists() and final_path.stat().st_size > 0 else raw_path
            except Exception as e:
                logger.warning(f"Re-encode failed, using raw: {e}")
                send_path = raw_path

            stop.set()
            await query.edit_message_text("📤 جاري إرسال الفيديو…")
            try:
                with open(send_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id, video=f,
                        caption=f"{pinfo['icon']} {title}",
                        supports_streaming=True,
                    )
                await query.delete_message()
                await send_extracted_audio(
                    context, query.message.chat_id, str(send_path), title, pinfo["icon"], loop
                )
            except Exception as e:
                logger.error(f"TikTok send error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الفيديو.")
    finally:
        stop.set()
        spin_task.cancel()


# ── يوتيوب + إنستقرام: فيديو من زر ───────────────────────────────────────────
async def download_video(query, context, url: str, title: str, platform: str) -> None:
    pinfo        = PLATFORM_INFO.get(platform, PLATFORM_INFO["youtube"])
    stop         = asyncio.Event()
    cookies_file = None

    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    if platform == "instagram":
        cookies_file = make_ig_cookies_file()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(title)s.%(ext)s")
            ydl_opts        = build_video_opts(platform, output_template, cookies_file)
            ydl_opts["writeinfojson"] = True
            loop = asyncio.get_running_loop()

            try:
                await loop.run_in_executor(None, lambda: run_ydl(ydl_opts, url))
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

            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files  = [f for f in Path(tmpdir).glob("*")
                          if f.is_file() and f.suffix.lower() != ".json"]
            if not all_files:
                await query.edit_message_text("❌ لم يتم العثور على الملف. حاول مجدداً.")
                return

            mp4_files  = [f for f in all_files if f.suffix.lower() == ".mp4"]
            video_file = max(mp4_files or all_files, key=lambda f: f.stat().st_size)

            if platform == "instagram":
                final_path = Path(tmpdir) / "ig_final.mp4"
                try:
                    await loop.run_in_executor(None, reencode_h264, str(video_file), str(final_path))
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
                        chat_id=query.message.chat_id, video=f,
                        caption=f"{pinfo['icon']} {title}",
                        supports_streaming=True,
                    )
                await query.delete_message()
                await send_extracted_audio(
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


# ── بينترست: من زر ────────────────────────────────────────────────────────────
async def send_pinterest(query, context, session: dict) -> None:
    pinfo    = PLATFORM_INFO["pinterest"]
    title    = session["title"]
    pin_info = session.get("pin_info", {})
    pin_type = pin_info.get("type")
    stop     = asyncio.Event()

    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        if pin_type == "video":
            stop.set()
            await download_video(query, context, session["url"], title, "pinterest")
            return

        if pin_type in ("image", "video_url"):
            media_url = pin_info["url"]
            suffix    = ".mp4" if pin_type == "video_url" else ".jpg"

            with tempfile.TemporaryDirectory() as tmpdir:
                media_path = Path(tmpdir) / f"pinterest{suffix}"
                loop       = asyncio.get_running_loop()

                try:
                    await loop.run_in_executor(
                        None, download_url_to_file, media_url, str(media_path)
                    )
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
                                chat_id=query.message.chat_id, photo=f,
                                caption=f"{pinfo['icon']} {title}",
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id, video=f,
                                caption=f"{pinfo['icon']} {title}",
                                supports_streaming=True,
                            )
                    await query.delete_message()
                    if pin_type == "video_url":
                        await send_extracted_audio(
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


# ── يوتيوب: صوت MP3 من زر ─────────────────────────────────────────────────────
async def download_audio(query, context, url: str, title: str) -> None:
    stop      = asyncio.Event()
    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "format":        "bestaudio/best",
                "outtmpl":       os.path.join(tmpdir, "%(title)s.%(ext)s"),
                "quiet":         True,
                "no_warnings":   True,
                "writeinfojson": True,
                "postprocessors": [{
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   "mp3",
                    "preferredquality": "192",
                }],
            }
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, lambda: run_ydl(ydl_opts, url))
            except Exception as e:
                logger.error(f"Audio download error: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء التحميل. حاول مجدداً.")
                return

            for jf in Path(tmpdir).glob("*.info.json"):
                try:
                    data  = json.loads(jf.read_text())
                    title = data.get("title") or data.get("id") or title
                except Exception:
                    pass
                break

            all_files  = [f for f in Path(tmpdir).glob("*")
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
                        chat_id=query.message.chat_id, audio=f,
                        title=title, caption=f"🎵 {title}",
                    )
                await query.delete_message()
            except Exception as e:
                logger.error(f"Error sending audio: {e}")
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الملف.")
    finally:
        stop.set()
        spin_task.cancel()
