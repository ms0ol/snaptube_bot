import re
import time
import asyncio
import tempfile
import logging

from config import SESSIONS, SESSION_TTL, WAIT_MSGS, IG_SESSIONID
from config import YOUTUBE_REGEX, TIKTOK_REGEX, INSTAGRAM_REGEX, PINTEREST_REGEX

logger = logging.getLogger(__name__)


def cleanup_sessions() -> None:
    now     = time.time()
    expired = [sid for sid, s in SESSIONS.items() if now - s.get("ts", 0) > SESSION_TTL]
    for sid in expired:
        del SESSIONS[sid]


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


def make_ig_cookies_file(sessionid: str = IG_SESSIONID) -> str:
    content = (
        "# Netscape HTTP Cookie File\n"
        f".instagram.com\tTRUE\t/\tTRUE\t2999999999\tsessionid\t{sessionid}\n"
        ".instagram.com\tTRUE\t/\tFALSE\t2999999999\tds_user_id\t0\n"
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(content)
    tmp.close()
    return tmp.name


async def spinner(msg, stop_event: asyncio.Event) -> None:
    idx = 0
    while not stop_event.is_set():
        await asyncio.sleep(4)
        if stop_event.is_set():
            break
        idx = (idx + 1) % len(WAIT_MSGS)
        try:
            await msg.edit_text(WAIT_MSGS[idx])
        except Exception:
            pass
