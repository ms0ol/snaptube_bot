import re
import time
import logging

import httpx
import yt_dlp

from config import TIKWM_API, PINTEREST_WIDGETS_API

logger = logging.getLogger(__name__)


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
