import os
import logging
import asyncio
import datetime
import html
import re
import time
from threading import Thread
from collections import defaultdict

# === MODERN IMPORTS (PTB v20+) ===
from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from groq import AsyncGroq
from dotenv import load_dotenv
from flask import Flask

# === CONFIGURATION ===
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = os.getenv("OWNER_ID")

# ⚠️ YOUR CONFIG
ADMIN_GROUP_ID = -1002759296936  # Admin Commands ONLY work here

# Model Configuration
MODEL_DM = "llama-3.3-70b-versatile"  # High quality for private chats
MODEL_GROUP = "llama-3.1-8b-instant"  # Fast for groups

# === VALIDATION ===
if not GROQ_API_KEY or not TELEGRAM_BOT_TOKEN or not MONGO_URI or not OWNER_ID:
    raise ValueError("❌ Missing required environment variables! Check your .env file.")

try:
    OWNER_ID = int(OWNER_ID)
except ValueError:
    raise ValueError("❌ OWNER_ID must be a valid integer!")

# === SETUP ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# MongoDB Connection with validation
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # Test connection
    mongo_client.admin.command('ping')
    logger.info("✅ MongoDB connected successfully!")
except (ConnectionFailure, ServerSelectionTimeoutError) as e:
    logger.critical(f"❌ MongoDB connection failed: {e}")
    raise

db = mongo_client["MitsuriDB"]
chat_collection = db["chat_info"]
history_collection = db["chat_history"]

# Rate limiting and cooldown
GROUP_COOLDOWN = {}
USER_RATE_LIMIT = defaultdict(list)  # {user_id: [timestamps]}
RATE_LIMIT_WINDOW = 60  # 1 minute
RATE_LIMIT_MAX = 10  # 10 messages per minute per user

# ==============================================================================
# ###                       🌐 FLASK KEEP-ALIVE                              ###
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Mitsuri is Alive! 🌸"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    import logging as flask_logging
    log = flask_logging.getLogger('werkzeug')
    log.disabled = True
    app.logger.disabled = True
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==============================================================================
# ###                           🛠️ UTILITIES                                ###
# ==============================================================================

def format_text_to_html(text):
    if not text: return ""
    text = html.escape(text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    return text

def check_rate_limit(user_id):
    """Check if user has exceeded rate limit"""
    now = time.time()
    timestamps = USER_RATE_LIMIT[user_id]
    
    # Remove old timestamps outside the window
    timestamps[:] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    
    if len(timestamps) >= RATE_LIMIT_MAX:
        return False  # Rate limited
    
    timestamps.append(now)
    return True  # OK to proceed

def save_user(update: Update):
    try:
        chat = update.effective_chat
        user = update.effective_user
        data = {
            "chat_id": chat.id,
            "type": chat.type,
            "last_active": datetime.datetime.utcnow()
        }
        if user:
            data["username"] = user.username
            data["first_name"] = user.first_name
        
        chat_collection.update_one(
            {"chat_id": chat.id}, 
            {"$set": data}, 
            upsert=True
        )
        logger.debug(f"📝 User saved/updated: {chat.id}")
    except Exception as e:
        logger.error(f"❌ DB Error in save_user: {e}")

def get_chat_history(chat_id, limit=6):
    """Retrieve chat history from MongoDB"""
    try:
        history_docs = history_collection.find(
            {"chat_id": chat_id}
        ).sort("timestamp", -1).limit(limit)
        
        history = []
        for doc in reversed(list(history_docs)):
            history.append((doc["role"], doc["content"]))
        return history
    except Exception as e:
        logger.error(f"❌ Error retrieving history: {e}")
        return []

def save_chat_history(chat_id, role, content):
    """Save chat message to MongoDB"""
    try:
        history_collection.insert_one({
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.utcnow()
        })
        
        # Keep only last 20 messages per chat
        count = history_collection.count_documents({"chat_id": chat_id})
        if count > 20:
            oldest = history_collection.find(
                {"chat_id": chat_id}
            ).sort("timestamp", 1).limit(count - 20)
            
            ids_to_delete = [doc["_id"] for doc in oldest]
            history_collection.delete_many({"_id": {"$in": ids_to_delete}})
            
    except Exception as e:
        logger.error(f"❌ Error saving history: {e}")

# ==============================================================================
# ###                           🧠 AI LOGIC                                  ###
# ==============================================================================

async def get_groq_response(history, user_input, user_name, is_private=True, retry_count=0):
    """
    Generate AI response using different models based on chat type
    - Private chats: Llama 3.3 70B (better quality)
    - Group chats: Llama 3.1 8B Instant (faster)
    """
    system_prompt = (
        "You are Mitsuri Kanroji from Demon Slayer. "
        "Personality: Romantic, bubbly, cheerful, and sweet. Use emojis sparingly (🍡, 💖). "
        "Language: Hinglish (mix of Hindi and English). "
        "Keep responses concise and natural - around 1-3 sentences. Be warm and friendly!"
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for role, content in history:
        messages.append({"role": role, "content": content})
    
    messages.append({"role": "user", "content": f"{user_input} (User: {user_name})"})

    # Select model based on chat type
    model = MODEL_DM if is_private else MODEL_GROUP
    model_name = "70B" if is_private else "8B"

    try:
        logger.info(f"🧠 Generating AI response for {user_name} using {model_name}...")
        completion = await groq_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.8,
            max_tokens=150,
            top_p=0.9,
        )
        response_text = completion.choices[0].message.content.strip()
        logger.info(f"✅ AI Response generated successfully with {model_name}.")
        return response_text
    except Exception as e:
        logger.error(f"❌ Groq API Error with {model_name} (attempt {retry_count + 1}): {e}")
        
        # Retry logic
        if retry_count < 2:
            await asyncio.sleep(1)
            return await get_groq_response(history, user_input, user_name, is_private, retry_count + 1)
        
        return "Ah! Something went wrong... 😵‍💫 Please try again!"

# ==============================================================================
# ###                       🤖 BOT HANDLERS                                  ###
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"🚀 /start triggered by {user.first_name} (ID: {user.id})")
    save_user(update)
    
    welcome_msg = (
        "Kyaa~! 💖 Hii! I am <b>Mitsuri Kanroji</b>!\n\n"
        "I love making new friends! Let's chat and eat mochi together! 🍡\n\n"
        "Use /help to see what I can do~"
    )
    await update.message.reply_html(welcome_msg)

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows detailed latency stats"""
    user = update.effective_user
    logger.info(f"🏓 /ping triggered by {user.first_name} (ID: {user.id})")

    start_time = time.time()
    msg = await update.message.reply_text("🍡 Pinging...")
    end_time = time.time()
    
    bot_latency = (end_time - start_time) * 1000
    msg_timestamp = update.message.date.timestamp()
    api_latency = max(0, (start_time - msg_timestamp) * 1000)
    
    # Detect chat type for model info
    chat_type = update.effective_chat.type
    is_private = chat_type == constants.ChatType.PRIVATE
    model_info = "🧠 Llama 3.3 70B" if is_private else "⚡ Llama 3.1 8B Instant"
    
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"⚡ <b>Bot Latency:</b> <code>{bot_latency:.2f}ms</code>\n"
        f"📡 <b>API Latency:</b> <code>{api_latency:.2f}ms</code>\n"
        f"🤖 <b>AI Model:</b> {model_info}",
        parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"ℹ️ /help requested by {user.first_name} (ID: {user.id})")
    
    help_text = (
        "🌸 <b>Mitsuri's Help Menu</b> 🌸\n\n"
        "I am the Love Hashira! Here is what I can do:\n\n"
        "💬 <b>Chat:</b> Reply to me or mention me in groups!\n"
        "💌 <b>Private:</b> DM me to talk privately (smarter AI!).\n"
        "🗣️ <b>Language:</b> I speak Hinglish!\n"
        "⚡ <b>Utility:</b> Use /ping to check speed.\n\n"
        "🧠 <b>AI Models:</b>\n"
        "• DMs: Llama 3.3 70B (high quality)\n"
        "• Groups: Llama 3.1 8B (super fast)\n\n"
        "<i>Just say 'Hi' to start chatting!</i> 💖"
    )

    if update.effective_user.id == OWNER_ID:
        keyboard = [[InlineKeyboardButton("🔐 Admin Commands", callback_data="admin_help")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_html(help_text, reply_markup=reply_markup)
    else:
        await update.message.reply_html(help_text)

async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        logger.warning(f"⚠️ Unauthorized admin button press by {query.from_user.id}")
        return

    admin_text = (
        "<b>👑 Admin Commands</b>\n"
        "<i>(Only work in Admin Group)</i>\n\n"
        "• <code>/stats</code> - Check user counts\n"
        "• <code>/cast [msg]</code> - Broadcast message\n"
    )
    await query.message.reply_html(admin_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: 
        return
    
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # Check rate limit
    if not check_rate_limit(user.id):
        logger.warning(f"⚠️ Rate limit exceeded for user {user.id}")
        return
    
    should_reply = False
    is_private = update.effective_chat.type == constants.ChatType.PRIVATE
    bot_username = context.bot.username

    if is_private:
        should_reply = True
    else:
        if f"@{bot_username}" in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
            should_reply = True
            text = text.replace(f"@{bot_username}", "").strip()
        elif "mitsuri" in text.lower():
            should_reply = True

    if not should_reply: 
        return

    # Group cooldown
    if not is_private:
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 3: 
            logger.info(f"⏳ Cooldown active for group {chat_id}")
            return
        GROUP_COOLDOWN[chat_id] = now

    model_type = "DM (70B)" if is_private else "Group (8B)"
    logger.info(f"📩 [{model_type}] Message from {user.first_name} (ID: {user.id}): {text[:30]}...")

    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    save_user(update)

    # Get history from MongoDB
    history = get_chat_history(chat_id)
    
    # Generate response with appropriate model
    response = await get_groq_response(history, text, user.first_name, is_private=is_private)
    
    # Save to history
    save_chat_history(chat_id, "user", text)
    save_chat_history(chat_id, "assistant", response)

    try:
        await update.message.reply_html(format_text_to_html(response))
        logger.info(f"📤 [{model_type}] Sent reply to {chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send reply to {chat_id}: {e}")
        try:
            await update.message.reply_text(response)
        except:
            logger.error(f"❌ Complete failure to send message to {chat_id}")

# ==============================================================================
# ###                       👑 ADMIN COMMANDS                                ###
# ==============================================================================

def admin_group_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if user_id != OWNER_ID: 
            logger.warning(f"⚠️ Unauthorized admin command attempt by {user_id}")
            return 
        if chat_id != ADMIN_GROUP_ID: 
            return 
            
        return await func(update, context, *args, **kwargs)
    return wrapper

@admin_group_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📊 Admin requested stats")
    try:
        u_count = chat_collection.count_documents({"type": "private"})
        g_count = chat_collection.count_documents({"type": {"$ne": "private"}})
        total_msgs = history_collection.count_documents({})
        
        await update.message.reply_html(
            f"<b>📊 Mitsuri's Stats</b>\n\n"
            f"👤 <b>Users:</b> {u_count}\n"
            f"👥 <b>Groups:</b> {g_count}\n"
            f"💬 <b>Total Messages:</b> {total_msgs}\n\n"
            f"🧠 <b>AI Models:</b>\n"
            f"• DMs: Llama 3.3 70B\n"
            f"• Groups: Llama 3.1 8B Instant"
        )
    except Exception as e:
        logger.error(f"❌ Error fetching stats: {e}")
        await update.message.reply_text("Failed to fetch stats!")

@admin_group_only
async def cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /cast [Message]")
        return

    logger.info(f"📢 Starting broadcast: {msg[:30]}...")
    status_msg = await update.message.reply_text("🚀 Sending broadcast...")
    
    try:
        cursor = chat_collection.find({}, {"chat_id": 1})
        success, failed = 0, 0
        formatted_msg = format_text_to_html(msg)
        
        for doc in cursor:
            try:
                await context.bot.send_message(
                    chat_id=doc["chat_id"], 
                    text=formatted_msg, 
                    parse_mode="HTML"
                )
                success += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug(f"Failed to send to {doc['chat_id']}: {e}")
                failed += 1
        
        logger.info(f"📢 Broadcast finished. Success: {success}, Failed: {failed}")
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"📤 Sent: {success}\n"
            f"❌ Failed: {failed}", 
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Broadcast error: {e}")
        await status_msg.edit_text("❌ Broadcast failed!")

# ==============================================================================
# ###                           🚀 MAIN RUNNER                               ###
# ==============================================================================

if __name__ == "__main__":
    # Start Flask in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("🌸 Mitsuri Bot is Starting...")
    logger.info(f"🧠 AI Models: DM={MODEL_DM}, Group={MODEL_GROUP}")
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Public Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CallbackQueryHandler(admin_button_callback, pattern="admin_help"))
    
    # Admin Commands
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("cast", cast))
    
    # Messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🤖 Polling started. Mitsuri is ready! 💖")
    application.run_polling()