import os
import logging
import asyncio
import datetime
import html
import re
import time
from threading import Thread

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
from groq import AsyncGroq
from dotenv import load_dotenv
from flask import Flask

# === CONFIGURATION ===
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# ⚠️ YOUR CONFIG
OWNER_ID = int(os.getenv("OWNER_ID", "0")) 
ADMIN_GROUP_ID = -1002759296936  # Admin Commands ONLY work here

# === SETUP ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if not GROQ_API_KEY or not TELEGRAM_BOT_TOKEN:
    logger.critical("❌ Missing API Keys!")

groq_client = AsyncGroq(api_key=GROQ_API_KEY)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_collection = db["chat_info"]

GROUP_COOLDOWN = {}

# ==============================================================================
# ###                       🌐 FLASK KEEP-ALIVE                              ###
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Mitsuri is Alive! 🌸"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

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
    except Exception as e:
        logger.error(f"DB Error: {e}")

# ==============================================================================
# ###                           🧠 AI LOGIC                                  ###
# ==============================================================================

async def get_groq_response(history, user_input, user_name):
    system_prompt = (
        "You are Mitsuri Kanroji (Love Hashira). "
        "Personality: Romantic, bubbly, uses emojis (🍡, 💖). "
        "Language: Hinglish. Keep it short (max 40 words)."
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for role, content in history:
        messages.append({"role": role, "content": content})
    
    messages.append({"role": "user", "content": f"{user_input} (User: {user_name})"})

    try:
        completion = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=300,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq Error: {e}")
        return "Ah! Something went wrong... 😵‍💫"

# ==============================================================================
# ###                       🤖 BOT HANDLERS                                  ###
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update)
    await update.message.reply_html("Hii! I am <b>Mitsuri Kanroji</b>! 💖\nLet's eat mochi together!")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows detailed latency stats.
    Accessible by everyone.
    """
    start_time = time.time()
    
    # 1. Send initial message
    msg = await update.message.reply_text("🍡 Pinging...")
    
    end_time = time.time()
    
    # 2. Calculate Latency (Bot Response Time)
    bot_latency = (end_time - start_time) * 1000 # Convert to ms
    
    # 3. Calculate API Latency (Approximate distance from Telegram Servers)
    # Note: This depends on server clock sync
    msg_timestamp = update.message.date.timestamp()
    api_latency = (start_time - msg_timestamp) * 1000
    if api_latency < 0: api_latency = 0 # Prevent negative numbers if clock skewed
    
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"⚡ <b>Bot Latency:</b> <code>{bot_latency:.2f}ms</code>\n"
        f"📡 <b>API Latency:</b> <code>{api_latency:.2f}ms</code>",
        parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🌸 <b>Mitsuri's Help Menu</b> 🌸\n\n"
        "I am the Love Hashira! Here is what I can do:\n"
        "🍡 <b>Chat:</b> Reply to me or mention me in groups!\n"
        "🍡 <b>Private:</b> DM me to talk privately.\n"
        "🍡 <b>Language:</b> I speak English & Hindi (Hinglish).\n"
        "🍡 <b>Utility:</b> Use /ping to check speed.\n\n"
        "<i>Just say 'Hi' to start!</i> 💖"
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
        return

    admin_text = (
        "<b>👑 Admin Commands</b>\n"
        "<i>(Only work in Admin Group)</i>\n\n"
        "• <code>/stats</code> - Check user counts\n"
        "• <code>/cast [msg]</code> - Broadcast message\n"
    )
    await query.message.reply_html(admin_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    
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

    if not should_reply: return

    if not is_private:
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 3: return
        GROUP_COOLDOWN[chat_id] = now

    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    save_user(update)

    history = context.chat_data.get("history", [])
    response = await get_groq_response(history, text, user.first_name)
    
    history.append(("user", text))
    history.append(("assistant", response))
    if len(history) > 6: history = history[-6:]
    context.chat_data["history"] = history

    try:
        await update.message.reply_html(format_text_to_html(response))
    except Exception:
        await update.message.reply_text(response)

# ==============================================================================
# ###                       👑 ADMIN COMMANDS (SILENT)                       ###
# ==============================================================================

def admin_group_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if user_id != OWNER_ID: return 
        if chat_id != ADMIN_GROUP_ID: return 
            
        return await func(update, context, *args, **kwargs)
    return wrapper

@admin_group_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_count = chat_collection.count_documents({"type": "private"})
    g_count = chat_collection.count_documents({"type": {"$ne": "private"}})
    await update.message.reply_html(f"<b>📊 Stats</b>\n\n👤 Users: {u_count}\n👥 Groups: {g_count}")

@admin_group_only
async def cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /cast [Message]")
        return

    status_msg = await update.message.reply_text("🚀 Sending...")
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
        except Exception:
            failed += 1
            
    await status_msg.edit_text(f"✅ <b>Done</b>\nSent: {success}\nFailed: {failed}", parse_mode="HTML")

# ==============================================================================
# ###                           🚀 MAIN RUNNER                               ###
# ==============================================================================

if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    print("🌸 Mitsuri Bot is Starting...")
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

    application.run_polling()
