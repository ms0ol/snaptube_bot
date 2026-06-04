from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 أهلاً بك!\n\n"
        "📥 أرسل لي أي رابط من المنصات التالية وسأحمّله لك مباشرةً:\n\n"
        "▶️ يوتيوب — فيديو حتى 1080p أو صوت MP3\n"
        "🎵 تيك توك — بدون علامة مائية\n"
        "📸 إنستقرام — ريلز وفيديوهات وصور\n"
        "📌 بينترست — فيديوهات وصور\n\n"
        "💡 فقط أرسل الرابط وسأتولى الباقي!",
        parse_mode="Markdown",
    )
