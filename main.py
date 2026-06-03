import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from handlers.commands   import start
from handlers.url_handler import handle_message, button_handler
from handlers.converters  import (
    handle_voice,
    handle_audio_to_voice,
    handle_photo_to_sticker,
    handle_sticker_to_photo,
    handle_video_convert,
    handle_video_note_convert,
    handle_document_to_pdf,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in Replit Secrets!")

    app = Application.builder().token(token).build()

    # ── أوامر ─────────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))

    # ── روابط وأزرار ──────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

    # ── محوّلات الوسائط ────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.VOICE,        handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO,        handle_audio_to_voice))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo_to_sticker))
    app.add_handler(MessageHandler(filters.Sticker.ALL,  handle_sticker_to_photo))
    app.add_handler(MessageHandler(filters.VIDEO,        handle_video_convert))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE,   handle_video_note_convert))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_to_pdf))

    # ── تشغيل البوت ───────────────────────────────────────────────────────────
    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    domain = (
        (os.environ.get("REPLIT_DOMAINS", "").split(",")[0].strip()
         or os.environ.get("REPLIT_DEV_DOMAIN", ""))
        if is_production
        else os.environ.get("REPLIT_DEV_DOMAIN", "")
    )

    if domain:
        webhook_path = f"/webhook/{token}"
        webhook_url  = f"https://{domain}{webhook_path}"
        logger.info(f"{'Production' if is_production else 'Development'} webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=8080,
            url_path=webhook_path,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Fallback: polling mode")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
