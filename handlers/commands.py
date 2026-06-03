from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يرد على أمر /start بقائمة المنصات والميزات المدعومة."""
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
