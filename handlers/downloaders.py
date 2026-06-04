"""
دوال تحميل الوسائط من جميع المنصات.
"""

import os
import json
import asyncio
import tempfile
import logging
from pathlib import Path

from config import PLATFORM_INFO, MAX_FILE_SIZE, WAIT_MSGS
from utils import spinner, make_ig_cookies_file
from media_tools import (
    reencode_for_telegram,
    extract_mp3,
    download_url_to_file,
    build_video_opts,
    build_audio_opts,
    run_ydl,
)

logger = logging.getLogger(__name__)


async def _send_video_file(context, chat_id: int, path: Path, caption: str) -> bool:
    try:
        with open(path, "rb") as f:
            await context.bot.send_video(
                chat_id=chat_id, video=f,
                caption=caption,
                supports_streaming=True,
            )
        return True
    except Exception as e:
        logger.error(f"send_video error: {e}")
        return False


async def _send_audio_file(context, chat_id: int, path: Path,
                           title: str, caption: str) -> bool:
    try:
        with open(path, "rb") as f:
            await context.bot.send_audio(
                chat_id=chat_id, audio=f,
                title=title, caption=caption,
            )
        return True
    except Exception as e:
        logger.error(f"send_audio error: {e}")
        return False


async def _download_tiktok_video(session: dict, tmpdir: str, loop) -> Path | None:
    tk_data   = session.get("tiktok_data", {})
    video_url = tk_data.get("play") or tk_data.get("wmplay")
    if not video_url:
        return None

    raw_path   = Path(tmpdir) / "tiktok_raw.mp4"
    final_path = Path(tmpdir) / "tiktok.mp4"

    try:
        await loop.run_in_executor(None, download_url_to_file, video_url, str(raw_path))
    except Exception as e:
        logger.error(f"TikTok download error: {e}")
        return None

    if raw_path.stat().st_size > MAX_FILE_SIZE:
        return None

    try:
        await loop.run_in_executor(None, reencode_for_telegram, str(raw_path), str(final_path))
        return final_path if final_path.exists() and final_path.stat().st_size > 0 else raw_path
    except Exception as e:
        logger.warning(f"TikTok re-encode failed: {e}")
        return raw_path


async def _download_ydl_video(url: str, platform: str, tmpdir: str,
                               loop) -> tuple[Path | None, str]:
    cookies_file = None
    if platform == "instagram":
        cookies_file = make_ig_cookies_file()

    out_tmpl = str(Path(tmpdir) / "%(title)s.%(ext)s")
    opts     = build_video_opts(platform, out_tmpl, cookies_file)
    opts["writeinfojson"] = True

    try:
        await loop.run_in_executor(None, lambda: run_ydl(opts, url))
    except Exception as e:
        logger.error(f"ydl video error ({platform}): {e}")
        return None, platform
    finally:
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass

    title = PLATFORM_INFO[platform]["name"]
    for jf in Path(tmpdir).glob("*.info.json"):
        try:
            data  = json.loads(jf.read_text())
            title = data.get("title") or data.get("id") or title
        except Exception:
            pass
        break

    all_files  = [f for f in Path(tmpdir).glob("*")
                  if f.is_file() and f.suffix.lower() not in (".json",)]
    if not all_files:
        return None, title

    mp4_files  = [f for f in all_files if f.suffix.lower() == ".mp4"]
    video_file = max(mp4_files or all_files, key=lambda f: f.stat().st_size)

    final_path = Path(tmpdir) / "final_output.mp4"
    try:
        await loop.run_in_executor(
            None, reencode_for_telegram, str(video_file), str(final_path)
        )
        if final_path.exists() and final_path.stat().st_size > 0:
            video_file = final_path
    except Exception as e:
        logger.warning(f"Re-encode failed for {platform}: {e}")

    return video_file, title


async def _download_ydl_audio(url: str, platform: str, tmpdir: str,
                               loop) -> tuple[Path | None, str]:
    cookies_file = None
    if platform == "instagram":
        cookies_file = make_ig_cookies_file()

    out_tmpl = str(Path(tmpdir) / "%(title)s.%(ext)s")
    opts     = build_audio_opts(out_tmpl, cookies_file)
    opts["writeinfojson"] = True

    try:
        await loop.run_in_executor(None, lambda: run_ydl(opts, url))
    except Exception as e:
        logger.error(f"ydl audio error ({platform}): {e}")
        return None, platform
    finally:
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except Exception:
                pass

    title = PLATFORM_INFO[platform]["name"]
    for jf in Path(tmpdir).glob("*.info.json"):
        try:
            data  = json.loads(jf.read_text())
            title = data.get("title") or data.get("id") or title
        except Exception:
            pass
        break

    all_files = [f for f in Path(tmpdir).glob("*")
                 if f.is_file() and f.suffix.lower() not in (".json",)]
    if not all_files:
        return None, title

    mp3_files  = [f for f in all_files if f.suffix.lower() == ".mp3"]
    audio_file = max(mp3_files or all_files, key=lambda f: f.stat().st_mtime)
    return audio_file, title


async def handle_video_action(query, context, session: dict) -> None:
    platform = session["platform"]
    pinfo    = PLATFORM_INFO[platform]
    url      = session["url"]
    title    = session.get("title", pinfo["name"])
    chat_id  = query.message.chat_id
    loop     = asyncio.get_running_loop()

    stop      = asyncio.Event()
    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if platform == "tiktok":
                video_file = await _download_tiktok_video(session, tmpdir, loop)
            else:
                video_file, title = await _download_ydl_video(url, platform, tmpdir, loop)

            if not video_file:
                await query.edit_message_text("❌ تعذّر تحميل الفيديو.")
                return

            if video_file.stat().st_size > MAX_FILE_SIZE:
                await query.edit_message_text(
                    f"⚠️ حجم الفيديو ({video_file.stat().st_size // (1024*1024)} MB) "
                    "يتجاوز الحد المسموح (50 MB)."
                )
                return

            stop.set()
            await query.edit_message_text("📤 جاري إرسال الفيديو…")
            ok = await _send_video_file(context, chat_id, video_file, f"{pinfo['icon']} {title}")
            if ok:
                await query.delete_message()
            else:
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الفيديو.")
    finally:
        stop.set()
        spin_task.cancel()


async def handle_audio_action(query, context, session: dict) -> None:
    platform = session["platform"]
    pinfo    = PLATFORM_INFO[platform]
    url      = session["url"]
    title    = session.get("title", pinfo["name"])
    chat_id  = query.message.chat_id
    loop     = asyncio.get_running_loop()

    stop      = asyncio.Event()
    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if platform == "tiktok":
                video_file = await _download_tiktok_video(session, tmpdir, loop)
                if not video_file:
                    await query.edit_message_text("❌ تعذّر التحميل.")
                    return
                mp3_path = Path(tmpdir) / "audio.mp3"
                try:
                    await loop.run_in_executor(None, extract_mp3, str(video_file), str(mp3_path))
                    audio_file: Path | None = mp3_path
                except Exception as e:
                    logger.error(f"TikTok mp3 extract error: {e}")
                    await query.edit_message_text("❌ تعذّر استخراج الصوت.")
                    return
            else:
                audio_file, title = await _download_ydl_audio(url, platform, tmpdir, loop)
                if not audio_file:
                    await query.edit_message_text("❌ تعذّر تحميل الصوت.")
                    return

            if audio_file.stat().st_size > MAX_FILE_SIZE:
                await query.edit_message_text("⚠️ حجم الملف يتجاوز الحد المسموح (50 MB).")
                return

            stop.set()
            await query.edit_message_text("📤 جاري إرسال الصوت…")
            ok = await _send_audio_file(context, chat_id, audio_file, title, f"🎵 {title}")
            if ok:
                await query.delete_message()
            else:
                await query.edit_message_text("❌ حدث خطأ أثناء إرسال الصوت.")
    finally:
        stop.set()
        spin_task.cancel()


async def handle_both_action(query, context, session: dict) -> None:
    platform = session["platform"]
    pinfo    = PLATFORM_INFO[platform]
    url      = session["url"]
    title    = session.get("title", pinfo["name"])
    chat_id  = query.message.chat_id
    loop     = asyncio.get_running_loop()

    stop      = asyncio.Event()
    await query.edit_message_text(WAIT_MSGS[0])
    spin_task = asyncio.create_task(spinner(query.message, stop))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if platform == "tiktok":
                video_file = await _download_tiktok_video(session, tmpdir, loop)
                if not video_file:
                    await query.edit_message_text("❌ تعذّر التحميل.")
                    return
                mp3_path = Path(tmpdir) / "audio.mp3"
                try:
                    await loop.run_in_executor(None, extract_mp3, str(video_file), str(mp3_path))
                    audio_file: Path | None = mp3_path
                except Exception as e:
                    logger.warning(f"TikTok mp3 extract failed: {e}")
                    audio_file = None
            else:
                video_tmpdir = Path(tmpdir) / "video"
                audio_tmpdir = Path(tmpdir) / "audio"
                video_tmpdir.mkdir()
                audio_tmpdir.mkdir()

                video_task = asyncio.create_task(
                    _download_ydl_video(url, platform, str(video_tmpdir), loop)
                )
                audio_task = asyncio.create_task(
                    _download_ydl_audio(url, platform, str(audio_tmpdir), loop)
                )

                (video_file, title_v), (audio_file, title_a) = await asyncio.gather(
                    video_task, audio_task
                )
                title = title_v or title_a or title

            if video_file and video_file.exists() and video_file.stat().st_size <= MAX_FILE_SIZE:
                stop.set()
                await query.edit_message_text("📤 جاري إرسال الفيديو…")
                await _send_video_file(context, chat_id, video_file, f"{pinfo['icon']} {title}")
            else:
                await query.edit_message_text("⚠️ الفيديو أكبر من 50 MB، جاري إرسال الصوت فقط…")

            if audio_file and audio_file.exists() and audio_file.stat().st_size <= MAX_FILE_SIZE:
                await context.bot.send_chat_action(chat_id=chat_id, action="upload_document")
                await _send_audio_file(context, chat_id, audio_file, title, f"🎵 {title}")

            await query.delete_message()

    except Exception as e:
        logger.error(f"both action error: {e}")
        await query.edit_message_text("❌ حدث خطأ غير متوقع.")
    finally:
        stop.set()
        spin_task.cancel()
