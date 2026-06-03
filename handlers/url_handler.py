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
from handlers.downloaders import (
    inline_tiktok,
    inline_ydl,
    inline_pinterest,
    send_tiktok,
    send_pinterest,
    download_video,
    download_audio,
)

logger = logging.getLogger(__name__)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يستقبل الرسائل النصية، يكشف الرابط، ويبدأ التحميل المناسب."""
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
        cleanup_sessions()
        sid = str(uuid.uuid4())
        SESSIONS[sid] = {
            "url":      url,
            "title":    "يوتيوب",
            "platform": "youtube",
            "ts":       time.time(),
            "user_id":  update.message.from_user.id,
            "chat_id":  update.message.chat_id,
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
    status_msg = await update.message.reply_text(WAIT_MSGS[0])
    stop       = asyncio.Event()
    spin_task  = asyncio.create_task(spinner(status_msg, stop))
    try:
        if platform == "tiktok":
            await inline_tiktok(update, context, url, status_msg, stop, loop)
        elif platform == "instagram":
            await inline_ydl(update, context, url, "instagram", status_msg, stop, loop)
        elif platform == "pinterest":
            await inline_pinterest(update, context, url, status_msg, stop, loop)
    finally:
        stop.set()
        spin_task.cancel()


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يعالج ضغطات الأزرار التفاعلية (فيديو / صوت)."""
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

    platform = session.get("platform", "youtube")
    url      = session["url"]
    title    = session["title"]

    if action == "video":
        if platform == "tiktok":
            await send_tiktok(query, context, session)
        elif platform == "pinterest":
            await send_pinterest(query, context, session)
        elif platform == "instagram":
            await download_video(query, context, url, title, "instagram")
        else:
            await download_video(query, context, url, title, platform)
    elif action == "audio":
        await download_audio(query, context, url, title)
    else:
        await query.edit_message_text("❌ طلب غير معروف.")
