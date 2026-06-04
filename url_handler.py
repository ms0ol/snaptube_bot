"""
معالج الرسائل النصية (الروابط) وأزرار الاختيار.
"""

import asyncio
import time
import uuid
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PLATFORM_INFO, SESSIONS, SESSION_TTL, WAIT_MSGS
from utils import cleanup_sessions, detect_platform, spinner
from fetchers import get_tiktok_info
from handlers.downloaders import (
    handle_video_action,
    handle_audio_action,
    handle_both_action,
)

logger = logging.getLogger(__name__)

PLATFORM_LABELS = {
    "youtube":   "▶️ يوتيوب",
    "tiktok":    "🎵 تيك توك",
    "instagram": "📸 إنستقرام",
    "pinterest": "📌 بينترست",
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    platform, url = detect_platform(text)

    if not platform:
        await update.message.reply_text(
            "❌ الرابط غير مدعوم.\n\n"
            "المنصات المدعومة: يوتيوب · تيك توك · إنستقرام · بينترست"
        )
        return

    cleanup_sessions()
    sid   = str(uuid.uuid4())
    pinfo = PLATFORM_INFO[platform]
    label = PLATFORM_LABELS[platform]

    session_data: dict = {
        "url":      url,
        "title":    pinfo["name"],
        "platform": platform,
        "ts":       time.time(),
        "user_id":  update.message.from_user.id,
        "chat_id":  update.message.chat_id,
    }

    if platform == "tiktok":
        status = await update.message.reply_text("⏳ جاري التحقق من الرابط…")
        loop   = asyncio.get_running_loop()
        tk_info = await loop.run_in_executor(None, get_tiktok_info, url)
        await status.delete()
        if not tk_info:
            await update.message.reply_text("❌ تعذّر جلب الفيديو. تأكد من الرابط.")
            return
        session_data["tiktok_data"] = tk_info
        session_data["title"] = tk_info.get("title", "تيك توك")

    SESSIONS[sid] = session_data

    keyboard = [[
        InlineKeyboardButton("🎥 فيديو",    callback_data=f"video:{sid}"),
        InlineKeyboardButton("🎵 صوت",      callback_data=f"audio:{sid}"),
        InlineKeyboardButton("🎥🎵 كلاهما", callback_data=f"both:{sid}"),
    ]]
    await update.message.reply_text(
        f"{label} — اختر نوع التحميل:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if ":" not in data:
        await query.edit_message_text("❌ طلب غير صالح.")
        return

    action, session_id = data.split(":", 1)
    session = SESSIONS.get(session_id)

    if not session:
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return
    if session.get("user_id") and session["user_id"] != query.from_user.id:
        await query.answer("❌ هذا الطلب ليس لك.", show_alert=True)
        return
    if time.time() - session.get("ts", 0) > SESSION_TTL:
        del SESSIONS[session_id]
        await query.edit_message_text("❌ انتهت صلاحية هذا الطلب. أرسل الرابط مجدداً.")
        return

    if action == "video":
        await handle_video_action(query, context, session)
    elif action == "audio":
        await handle_audio_action(query, context, session)
    elif action == "both":
        await handle_both_action(query, context, session)
    else:
        await query.edit_message_text("❌ طلب غير معروف.")
