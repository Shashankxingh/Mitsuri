import os
import logging
import asyncio
import datetime
import html
import re
from threading import Thread

# === MODERN IMPORTS (PTB v20+) ===
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.error import Forbidden, BadRequest

# === DATABASE & AI ===
from pymongo import MongoClient
from groq import AsyncGroq  # Asynchronous Groq Client
from dotenv import load_dotenv

# === FLASK FOR RENDER (KEEPS BOT ALIVE) ===
from flask import Flask

# === CONFIGURATION ===
load_dotenv()

# ⚠️ ENV VARIABLES (Set these in Render Environment Variables)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # Replace 0 with your ID in .env

# === SETUP ===
# 1. Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Clients
if not GROQ_API_KEY or not TELEGRAM_BOT_TOKEN:
    logger.critical("❌ Missing API Keys! Check .env or Render Config.")

groq_client = AsyncGroq(api_key=GROQ_API_KEY)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_collection = db["chat_info"]

# 3. Constants
GROUP_COOLDOWN = {}

# ==============================================================================
# ###                       🌐 FLASK KEEP-ALIVE                              ###
# ==============================================================================
# Render needs a web server listening on a port to keep the service running.
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Mitsuri is Alive! 🌸"

def run_flask():
    # Render assigns a port automatically via the PORT env var
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
    """Saves user info to MongoDB (Synchronous but fast enough)."""
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
    
    # Add simple history
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # Logic: Reply in DM always, or in Group if mentioned/replied
    should_reply = False
    is_private = update.effective_chat.type == constants.ChatType.PRIVATE
    bot_username = context.bot.username

    if is_private:
        should_reply = True
    else:
        # Check mention or reply
        if f"@{bot_username}" in text or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id):
            should_reply = True
            text = text.replace(f"@{bot_username}", "").strip()
        elif "mitsuri" in text.lower():
            should_reply = True

    if not should_reply: return

    # Group Cooldown Check
    if not is_private:
        import time
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 3:
            return
        GROUP_COOLDOWN[chat_id] = now

    # Typing status
    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    save_user(update)

    # History Management
    history = context.chat_data.get("history", [])
    response = await get_groq_response(history, text, user.first_name)
    
    # Update History
    history.append(("user", text))
    history.append(("assistant", response))
    if len(history) > 6: history = history[-6:]
    context.chat_data["history"] = history

    # Send Reply
    try:
        await update.message.reply_html(format_text_to_html(response))
    except Exception:
        await update.message.reply_text(response)

# ==============================================================================
# ###                       👑 ADMIN COMMANDS                                ###
# ==============================================================================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    # Note: Count documents is synchronous, might block briefly. Acceptable for admin cmd.
    u_count = chat_collection.count_documents({"type": "private"})
    g_count = chat_collection.count_documents({"type": {"$ne": "private"}})
    
    await update.message.reply_html(f"<b>📊 Stats</b>\n\n👤 Users: {u_count}\n👥 Groups: {g_count}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast [Message]")
        return

    status_msg = await update.message.reply_text("🚀 Sending...")
    cursor = chat_collection.find({}, {"chat_id": 1})
    
    success = 0
    failed = 0
    
    # Iterate mongo cursor
    for doc in cursor:
        try:
            await context.bot.send_message(
                chat_id=doc["chat_id"], 
                text=f"📢 <b>Announcement:</b>\n\n{format_text_to_html(msg)}", 
                parse_mode="HTML"
            )
            success += 1
            await asyncio.sleep(0.05) # Flood limit safety
        except Exception:
            failed += 1
            
    await status_msg.edit_text(f"✅ <b>Completed</b>\nSent: {success}\nFailed: {failed}", parse_mode="HTML")

# ==============================================================================
# ###                           🚀 MAIN RUNNER                               ###
# ==============================================================================

if __name__ == "__main__":
    # 1. Start Flask Server in a separate thread (For Render)
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # 2. Start Telegram Bot
    print("🌸 Mitsuri Bot is Starting...")
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot
    application.run_polling()
