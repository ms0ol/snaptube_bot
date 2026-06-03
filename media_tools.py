import os
import subprocess
import logging
from pathlib import Path

import httpx
import yt_dlp

from config import MAX_FILE_SIZE

logger = logging.getLogger(__name__)


# ── ffmpeg ────────────────────────────────────────────────────────────────────

def reencode_h264(input_path: str, output_path: str) -> None:
    """يُعيد ترميز الفيديو بـ H.264 ليتوافق مع تيليجرام."""
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


def extract_mp3(video_path: str, output_path: str) -> None:
    """يستخرج الصوت من الفيديو ويحفظه MP3."""
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


async def send_extracted_audio(context, chat_id: int, video_path: str,
                                title: str, icon: str, loop) -> None:
    """يستخرج MP3 من الفيديو ويُرسله تلقائياً — لا يوقف العملية عند الخطأ."""
    try:
        audio_path = str(video_path).rsplit(".", 1)[0] + "_audio.mp3"
        await loop.run_in_executor(None, extract_mp3, str(video_path), audio_path)
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


# ── yt-dlp ────────────────────────────────────────────────────────────────────

def download_url_to_file(url: str, path: str) -> None:
    """يُنزّل ملفاً من رابط HTTP مباشر."""
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


def build_video_opts(platform: str, output_template: str,
                     cookies_file: str | None = None) -> dict:
    """يبني إعدادات yt-dlp لتحميل الفيديو حسب المنصة."""
    base: dict = {
        "outtmpl":             output_template,
        "quiet":               True,
        "no_warnings":         True,
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


def run_ydl(ydl_opts: dict, url: str) -> None:
    """يُشغّل yt-dlp بالإعدادات والرابط المُعطَيَين."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
