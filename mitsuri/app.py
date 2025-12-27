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

from mitsuri.background_tasks import BackgroundWorker
from mitsuri.cache import cache
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
from mitsuri.storage import create_mongo_client, initialize_indexes

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


@app.route("/health")
def health_detailed():
    """Detailed health check endpoint."""
    return {
        "status": "healthy",
        "service": "mitsuri-bot",
        "version": "2.0.0-optimized"
    }


def run_flask():
    """Run Flask server in background thread."""
    port = int(os.environ.get("PORT", 8080))
    import logging as flask_logging

    log = flask_logging.getLogger("werkzeug")
    log.disabled = True
    app.logger.disabled = True
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


async def post_init(application):
    """Initialize async components after bot starts."""
    logger.info("🔧 Initializing async components...")
    
    # Initialize Redis cache
    await cache.initialize()
    
    # Start background workers
    state = application.bot_data["state"]
    background_worker = BackgroundWorker(state.history_collection)
    await background_worker.start()
    
    # Store worker reference for cleanup
    application.bot_data["background_worker"] = background_worker
    
    logger.info("✅ Async components initialized")


async def post_shutdown(application):
    """Cleanup async components on shutdown."""
    logger.info("🧹 Cleaning up async components...")
    
    # Stop background workers
    if "background_worker" in application.bot_data:
        await application.bot_data["background_worker"].stop()
    
    # Close Redis connection
    await cache.close()
    
    logger.info("✅ Cleanup complete")


def run():
    """Main entry point with optimized initialization."""
    owner_id = require_env()
    
    # Initialize MongoDB with connection pooling
    mongo_client = create_mongo_client()
    db = mongo_client["MitsuriDB"]
    chat_collection = db["chat_info"]
    history_collection = db["chat_history"]
    
    # Create database indexes for performance
    initialize_indexes(db)

    # Build bot state
    state = build_state(chat_collection, history_collection, owner_id, ADMIN_GROUP_ID)

    # Start Flask health check server
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("🌸 Mitsuri Bot is Starting...")
    logger.info("🧠 AI Models: Large=%s, Small=%s", MODEL_LARGE, MODEL_SMALL)
    logger.info("⚡ Performance Mode: OPTIMIZED")

    # Build Telegram application
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)  # Enable concurrent update processing
        .post_init(post_init)      # Initialize async components
        .post_shutdown(post_shutdown)  # Cleanup on shutdown
        .build()
    )
    
    application.bot_data["state"] = state

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CallbackQueryHandler(admin_button_callback, pattern="admin_help"))

    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("cast", cast))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Polling started. Mitsuri is ready! 💖")
    application.run_polling(drop_pending_updates=True)