import logging
import os
from threading import Thread

from flask import Flask
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from mitsuri.config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_GROUP_ID,
    MODEL_LARGE,
    MODEL_SMALL,
    require_env,
)
from mitsuri.handlers import (
    admin_button_callback,
    cast,
    handle_message,
    help_command,
    ping_command,
    start,
    stats,
    build_state,
)
from mitsuri.storage import create_mongo_client

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def health_check():
    return "Mitsuri is Alive! 🌸"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    import logging as flask_logging

    log = flask_logging.getLogger("werkzeug")
    log.disabled = True
    app.logger.disabled = True
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def run():
    owner_id = require_env()
    mongo_client = create_mongo_client()
    db = mongo_client["MitsuriDB"]
    chat_collection = db["chat_info"]
    history_collection = db["chat_history"]

    state = build_state(chat_collection, history_collection, owner_id, ADMIN_GROUP_ID)

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("🌸 Mitsuri Bot is Starting...")
    logger.info("🧠 AI Models: Large=%s, Small=%s", MODEL_LARGE, MODEL_SMALL)

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.bot_data["state"] = state

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CallbackQueryHandler(admin_button_callback, pattern="admin_help"))

    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("cast", cast))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Polling started. Mitsuri is ready! 💖")
    application.run_polling()
