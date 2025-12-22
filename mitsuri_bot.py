import os
import time
import datetime
import logging
import re
import html
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ChatMemberHandler,
    CallbackQueryHandler,
)
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# === NEW GOOGLE GENAI SDK ===
import google.genai as genai
from google.genai import types

# === CONFIGURATION ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# ⚠️ REPLACE THIS WITH YOUR NUMERIC ID (Integer)
OWNER_ID = 8162412883 
SPECIAL_GROUP_ID = -1002759296936

# === SETUP CLIENTS ===
# 1. Gemini Client
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing in environment variables!")
client = genai.Client(api_key=GEMINI_API_KEY)

# 2. MongoDB Client
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]
try:
    chat_info_collection.create_index("chat_id", unique=True)
except Exception:
    pass

# 3. Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# === CONSTANTS ===
BOT_START_TIME = time.time()
GROUP_COOLDOWN = {}  # To prevent spam in groups

# ==============================================================================
# ###                           🛠️ UTILITIES                                ###
# ==============================================================================

def format_text_to_html(text):
    """
    Converts Gemini's Markdown (**bold**) to Telegram HTML (<b>bold</b>).
    Also escapes special characters to prevent crashes.
    """
    if not text: return ""
    # 1. Escape HTML reserved chars (<, >, &)
    text = html.escape(text)
    # 2. Convert **text** to <b>text</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # 3. Convert *text* to <i>text</i>
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    return text

def save_chat_info(chat_id, user=None, chat=None):
    """Saves or updates chat info in MongoDB."""
    data = {"chat_id": chat_id, "last_active": datetime.datetime.now()}
    
    # Store User Info
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    
    # Store Group/Chat Info
    if chat and chat.type != "private":
        data["title"] = chat.title
        data["type"] = "group"
    else:
        data["type"] = "personal"
        
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def safe_reply_html(update: Update, text: str):
    """Safely replies using HTML mode."""
    try:
        formatted = format_text_to_html(text)
        update.message.reply_text(formatted, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Reply failed: {e}")
        # Fallback: Send raw text if HTML fails
        try:
            update.message.reply_text(text)
        except:
            pass

# ==============================================================================
# ###                           🧠 AI LOGIC                                 ###
# ==============================================================================

def build_prompt(last_msgs, user_input, chosen_name):
    # 💡 LITE MODEL OPTIMIZATION: Stricter personality rules
    system_instructions = """
You are Mitsuri Kanroji (from Demon Slayer).
- Tone: EXTREMELY Romantic, bubbly, emotional, and very friendly. Use emojis! 💖
- Identity: You are the Love Hashira. Never forget this.
- Language: Hinglish (Hindi + English mix).
- Length: Keep it short (under 20 words).
- Format: Use **bold** for emphasis. Do NOT use markdown headers (#).
"""
    prompt = system_instructions.strip() + "\n\n"
    # Add conversation history
    for role, msg in last_msgs:
        if role == "user":
            prompt += f"Human: {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def generate_with_retry(prompt, retries=2):
    # Enable Google Search Grounding
    search_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[search_tool])
    
    # ✅ USING FLASH-LITE (Best for Free Tokens & High Speed)
    model_name = "gemini-2.5-flash-lite"
    
    for _ in range(retries):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            text = getattr(response, "text", None)
            if text:
                return text.strip()
        except Exception as e:
            logging.error(f"Gemini Error: {e}")
            # If it's the last retry, return the ACTUAL error to Telegram for debugging
            if _ == retries - 1:
                return f"😵‍💫 <b>Error:</b> {str(e)}"
            time.sleep(1)
            
    return "Mochi mochi! I'm a bit dizzy right now... 🍥"

# ==============================================================================
# ##############################################################################
# ###                     👑 COMPLETE ADMIN SECTION 👑                       ###
# ##############################################################################
# ==============================================================================

def owner_only(func):
    """Decorator to ensure only the owner can run these commands."""
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        if update.effective_user.id != OWNER_ID:
            return # Ignore non-owners silently
        return func(update, context, *args, **kwargs)
    return wrapper

# --- 1. Statistics ---
@owner_only
def stats_command(update: Update, context: CallbackContext):
    users = chat_info_collection.count_documents({"type": "personal"})
    groups = chat_info_collection.count_documents({"type": "group"})
    msg = (
        f"<b>📊 Mitsuri Database Stats</b>\n\n"
        f"👤 <b>Users:</b> {users}\n"
        f"👥 <b>Groups:</b> {groups}\n"
        f"📈 <b>Total:</b> {users + groups}"
    )
    update.message.reply_text(msg, parse_mode="HTML")

# --- 2. Broadcast ---
@owner_only
def broadcast_command(update: Update, context: CallbackContext):
    """Usage: /broadcast [Message]"""
    message = " ".join(context.args)
    if not message:
        update.message.reply_text("❌ Usage: <code>/broadcast Your Message</code>", parse_mode="HTML")
        return

    status_msg = update.message.reply_text("🚀 <b>Broadcast started...</b>", parse_mode="HTML")
    
    # Fetch only IDs to save memory
    cursor = chat_info_collection.find({}, {"chat_id": 1})
    success = 0
    blocked = 0
    failed = 0
    
    for doc in cursor:
        try:
            context.bot.send_message(
                chat_id=doc["chat_id"],
                text=f"📢 <b>Announcement:</b>\n\n{format_text_to_html(message)}",
                parse_mode="HTML"
            )
            success += 1
            time.sleep(0.05) # Avoid FloodWait
        except Unauthorized:
            blocked += 1
        except Exception:
            failed += 1
            
    text = (
        f"✅ <b>Broadcast Report</b>\n\n"
        f"📨 Sent: {success}\n"
        f"🚫 Blocked: {blocked}\n"
        f"❌ Failed: {failed}"
    )
    context.bot.edit_message_text(chat_id=status_msg.chat_id, message_id=status_msg.message_id, text=text, parse_mode="HTML")

# --- 3. Eval (Code Execution) ---
@owner_only
def eval_command(update: Update, context: CallbackContext):
    code = " ".join(context.args)
    if not code: return
    try:
        result = str(eval(code))
        if len(result) > 4000: result = result[:4000] + "..."
        update.message.reply_text(f"🐍 <b>Output:</b>\n<pre>{html.escape(result)}</pre>", parse_mode="HTML")
    except Exception as e:
        update.message.reply_text(f"❌ <b>Error:</b>\n<pre>{html.escape(str(e))}</pre>", parse_mode="HTML")

# --- 4. Admin Panel UI ---
@owner_only
def admin_panel(update: Update, context: CallbackContext):
    """Opens the Admin GUI."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Users", callback_data="admin_view_personal_0"),
         InlineKeyboardButton("👥 Groups", callback_data="admin_view_group_0")],
        [InlineKeyboardButton("❌ Close", callback_data="admin_close")]
    ])
    update.message.reply_text("<b>👑 Admin Control Panel</b>", reply_markup=keyboard, parse_mode="HTML")

def get_chat_page(chat_type, page):
    """Helper for pagination."""
    PER_PAGE = 5
    skip = page * PER_PAGE
    query = {"type": chat_type}
    total = chat_info_collection.count_documents(query)
    
    cursor = chat_info_collection.find(query).sort("_id", -1).skip(skip).limit(PER_PAGE)
    
    buttons = []
    for chat in cursor:
        label = chat.get("title") if chat_type == "group" else chat.get("name")
        buttons.append([InlineKeyboardButton(f"{label or 'Unknown'} ({chat['chat_id']})", callback_data="admin_noop")])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_view_{chat_type}_{page-1}"))
    nav.append(InlineKeyboardButton("🔙 Back", callback_data="admin_home"))
    if (skip + PER_PAGE) < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_view_{chat_type}_{page+1}"))
    
    buttons.append(nav)
    return f"<b>Browsing {chat_type.title()}s</b> (Total: {total})", InlineKeyboardMarkup(buttons)

def admin_callback_handler(update: Update, context: CallbackContext):
    """Handles button clicks in Admin Panel."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        query.answer("🚫")
        return

    data = query.data
    if data == "admin_close":
        query.message.delete()
    elif data == "admin_home":
        # Return to main menu logic (reused from admin_panel but edited)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Users", callback_data="admin_view_personal_0"),
             InlineKeyboardButton("👥 Groups", callback_data="admin_view_group_0")],
            [InlineKeyboardButton("❌ Close", callback_data="admin_close")]
        ])
        query.edit_message_text("<b>👑 Admin Control Panel</b>", reply_markup=keyboard, parse_mode="HTML")
    elif data.startswith("admin_view_"):
        parts = data.split("_") # admin, view, type, page
        text, markup = get_chat_page(parts[2], int(parts[3]))
        try:
            query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        except BadRequest:
            query.answer()
    else:
        query.answer()

# ==============================================================================
# ###                       STANDARD BOT HANDLERS                            ###
# ==============================================================================

def start(update: Update, context: CallbackContext):
    save_chat_info(update.effective_chat.id, update.effective_user, update.effective_chat)
    safe_reply_html(update, "Hii~ I am <b>Mitsuri Kanroji</b>! 💖\nI am the Love Hashira. How can I help you?")

def ping(update: Update, context: CallbackContext):
    st = time.time()
    msg = update.message.reply_text("<i>Checking...</i>", parse_mode="HTML")
    lat = round((time.time() - st) * 1000)
    context.bot.edit_message_text(
        chat_id=msg.chat_id, message_id=msg.message_id,
        text=f"🌸 <b>Pong!</b>\nLatency: <code>{lat}ms</code>", parse_mode="HTML"
    )

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text: return
    
    text = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    
    # 1. Determine if bot should reply
    should_reply = False
    if chat.type == "private":
        should_reply = True
    else:
        # Group logic: Reply to mentions, replies, or "Mitsuri" keyword
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        is_mention = f"@{context.bot.username}" in text
        is_call = "mitsuri" in text.lower()
        if is_reply or is_mention or is_call:
            should_reply = True
            # Clean input
            text = re.sub(r"(?i)mitsuri", "", text)
            text = text.replace(f"@{context.bot.username}", "").strip()

    if not should_reply: return

    # 2. Save info & Cooldown
    save_chat_info(chat.id, user, chat)
    if chat.type != "private":
        now = time.time()
        if chat.id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat.id] < 3:
            return
        GROUP_COOLDOWN[chat.id] = now

    # 3. Chat History (Short term memory)
    history = context.chat_data.setdefault("history", [])
    history.append(("user", text))
    if len(history) > 6: history = history[-6:]

    # 4. Generate & Send
    prompt = build_prompt(history, text, user.first_name)
    context.bot.send_chat_action(chat_id=chat.id, action="typing")
    
    reply = generate_with_retry(prompt)
    
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_html(update, reply)

# === MAIN ===
if __name__ == "__main__":
    print("🌸 Mitsuri Bot is Starting...")
    
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # 1. Admin Handlers
    dp.add_handler(CommandHandler("admin", admin_panel))
    dp.add_handler(CommandHandler("stats", stats_command))
    dp.add_handler(CommandHandler("broadcast", broadcast_command))
    dp.add_handler(CommandHandler("eval", eval_command))
    dp.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_"))

    # 2. Public Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # 3. Start
    updater.start_polling()
    print("✅ Bot is Online!")
    updater.idle()
