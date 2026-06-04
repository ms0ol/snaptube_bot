import logging
from supabase import create_client
from telegram import Update
from telegram.ext import ContextTypes

from config import (
    DEVELOPER_ID,
    CHROMA_CHANNEL_ID,
    NATURE_CHANNEL_ID,
    SUPABASE_URL,
    SUPABASE_KEY,
)

logger = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


async def set_content_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != DEVELOPER_ID:
        return

    message = update.message
    replied = message.reply_to_message
    if not replied or (not replied.video and not replied.document):
        await message.reply_text("⚠️ يجب أن ترد (Reply) على رسالة تحتوي فيديو أو مستند.")
        return

    command     = message.text.split()[0].lstrip("/")
    parts       = message.text.split(maxsplit=1)
    description = parts[1].strip() if len(parts) > 1 else ""

    if command == "setContent_chroma":
        target_channel = CHROMA_CHANNEL_ID
        content_type   = "chroma"
    else:
        target_channel = NATURE_CHANNEL_ID
        content_type   = "nature"

    if replied.video:
        file_id        = replied.video.file_id
        file_unique_id = replied.video.file_unique_id
    else:
        file_id        = replied.document.file_id
        file_unique_id = replied.document.file_unique_id

    try:
        sent_msg = await context.bot.send_video(
            chat_id=target_channel,
            video=file_id,
            caption=description if description else None,
        )
    except Exception as e:
        logger.error(f"فشل إرسال الفيديو إلى القناة: {e}")
        await message.reply_text(f"❌ فشل إرسال الفيديو إلى القناة:\n{e}")
        return

    channel_id_str = str(target_channel).replace("-100", "")
    link = f"https://t.me/c/{channel_id_str}/{sent_msg.message_id}"

    try:
        supabase.table("videos").insert({
            "video_id":           file_unique_id,
            "file_id":            file_id,
            "type":               content_type,
            "description":        description,
            "channel_message_id": sent_msg.message_id,
            "link":               link,
        }).execute()
    except Exception as e:
        logger.error(f"فشل حفظ البيانات في Supabase: {e}")
        await message.reply_text(
            f"⚠️ تم نشر الفيديو في القناة، لكن فشل حفظه في قاعدة البيانات:\n{e}"
        )
        return

    await message.reply_text(
        f"✅ تم بنجاح!\n"
        f"📢 القناة: {'كروما' if content_type == 'chroma' else 'مناظر طبيعية'}\n"
        f"🔗 الرابط: {link}"
    )
