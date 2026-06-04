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

from handlers.commands    import start
from handlers.url_handler import handle_message, button_handler
from handlers.admin       import set_content_handler

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

    # ── أوامر عامة ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))

    # ── أوامر المطور ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("setContent_chroma", set_content_handler))
    app.add_handler(CommandHandler("setContent_nature", set_content_handler))

    # ── روابط وأزرار ──────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))

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
