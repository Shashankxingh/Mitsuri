import os
import time
import datetime
import logging
import re
import requests
from dotenv import load_dotenv
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
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
from telegram.utils.helpers import escape_markdown
from pymongo import MongoClient
import google-genai as genai
# === NEW IMPORT FOR GEMINI CONFIGURATION ===
from google.genai import types

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and group IDs ===
# NOTE: OWNER_ID must be an integer, not 8162412883 (too long) - assuming a placeholder for demonstration
OWNER_ID = 1234567890 
SPECIAL_GROUP_ID = -1002759296936

# === Gemini setup ===
genai.configure(api_key=GEMINI_API_KEY)
# Using the client for models.generate_content calls with configs
client = genai.Client()
# model = genai.GenerativeModel("models/gemini-2.5-flash-lite") # Removed: using client.models.generate_content instead

# === MongoDB setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]
chat_info_collection.create_index("chat_id", unique=True)

# === Logging ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# === Constants ===
REQUEST_DELAY = 2
BOT_START_TIME = time.time()
GROUP_COOLDOWN = {}

# === Utility ===
def get_main_menu_buttons():
    """Returns the main inline keyboard menu for chat browsing."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0")],
        [InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
    ])

def get_chat_list_buttons(chat_type, page, chats_per_page=10):
    """Fetches chats from DB and creates pagination/navigation buttons."""
    skip = page * chats_per_page
    
    # Determine the MongoDB query based on chat_type
    if chat_type == "personal":
        # Private chats are identified by having a user_id but typically no 'title'
        query = {"user_id": {"$exists": True}, "title": {"$exists": False}}
        data_prefix = "show_personal"
        display_key = "name"
    else: # group
        # Groups are identified by having a 'title'
        query = {"title": {"$exists": True}}
        data_prefix = "show_groups"
        display_key = "title"

    # Fetch total count and current page of chats
    total_chats = chat_info_collection.count_documents(query)
    chats_on_page = list(
        chat_info_collection.find(query)
        .sort([("_id", -1)]) # Sort by creation date (or insertion order)
        .skip(skip)
        .limit(chats_per_page)
    )
    
    # Create buttons for the current page
    chat_buttons = [
        [InlineKeyboardButton(
            f"{chat.get(display_key, 'Unknown')} (@{chat.get('chat_username', chat.get('username', 'N/A'))})", 
            callback_data=f"chat_detail_{chat['chat_id']}" # Placeholder for a detail view
        )]
        for chat in chats_on_page
    ]
    
    # Create navigation buttons
    nav_buttons = []
    total_pages = (total_chats + chats_per_page - 1) // chats_per_page
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{data_prefix}_{page - 1}"))
    
    if (page + 1) < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{data_prefix}_{page + 1}"))

    # Add back to main menu button
    back_button = [InlineKeyboardButton("🔙 Main Menu", callback_data="show_main_menu")]
    
    # Combine all buttons
    keyboard = chat_buttons
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append(back_button)
    
    return InlineKeyboardMarkup(keyboard), total_chats, skip, len(chats_on_page), total_pages

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id, "last_active": datetime.datetime.now()}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
        if chat.username:
            data["chat_username"] = chat.username
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def build_prompt(last_msgs, user_input, chosen_name):
    system_instructions = """
- Tum Mitsuri Kanroji ho (Demon Slayer se).
- Tum Hinglish mein short, cute aur thodi bubbly tone mein baat karti ho (under 30 words).
- Kabhi kabhi casually bolo ki tum [Shashank](https://t.me/shashankxingh) ki dost ho, par har baar nahi.
- *giggles* ya *actions* allowed nahi hain.
- Agar kuch nahi pata ho, web se concise info lao.
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_msgs:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

# === MODIFIED: Using Google Search Grounding Tool ===
def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    # 1. Define the search tool configuration
    search_tool = types.Tool(google_search=types.GoogleSearch())
    
    # 2. Create the generation configuration, enabling the tool
    config = types.GenerateContentConfig(
        tools=[search_tool]
    )
    
    # 3. Model to use with grounding
    model_name = "gemini-2.5-flash"
    
    for attempt in range(retries):
        try:
            start = time.time()
            # Use the global client and configuration
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

            text = getattr(response, "text", None)
            
            if text:
                reply = text.strip().replace("\n", " ")
                words = reply.split()
                if len(words) > 30:
                    reply = " ".join(words[:30]) + "..."
                return reply

            # If no text is returned, the model failed to generate a response
            return "Mujhe abhi exact info nahi mili, par main seekh rahi hu! 💖"
        
        except Exception as e:
            logging.error(f"Gemini error: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    
    return "Abhi main thoda busy hu... baad mein baat karte hain! 😊"
# ====================================================

def safe_reply_text(update: Update, text: str):
    try:
        # Simple attempt to clean up any unwanted markdown/LaTeX-like sequences
        text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
        text = escape_markdown(text, version=2)
        update.message.reply_text(text, parse_mode="MarkdownV2")
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Reply failed: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === Commands ===
def start(update: Update, context: CallbackContext):
    safe_reply_text(update, "Hii~ Mitsuri is here 💖 How can I help you today?")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return
    name = escape(update.message.from_user.first_name or "User")
    msg = update.message.reply_text("Checking latency...")
    try:
        model_name = "gemini-2.5-flash"
        start_api = time.time()
        # Using the new client for the ping
        gemini_reply = client.models.generate_content(model=model_name, contents="Say pong").text.strip()
        api_latency = round((time.time() - start_api) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)
        group_link = "https://t.me/mitsuri_homie"

        reply = (
            f"╭───[ 🌸 <b>Mitsuri Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>\n"
            f"├ Group: <a href='{group_link}'>@the_jellybeans</a>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Uptime: <b>{uptime}</b>\n"
            f"╰─ I'm here and responsive."
        )
        context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text=reply,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Something went wrong while checking ping.")

def eval_command(update: Update, context: CallbackContext):
    if update.message.from_user.id != OWNER_ID:
        return
    code = " ".join(context.args)
    try:
        result = str(eval(code))
        update.message.reply_text(f"✅ <b>Result:</b>\n<code>{escape(result)}</code>", parse_mode="HTML")
    except Exception as e:
        update.message.reply_text(f"❌ Error: <code>{escape(str(e))}</code>", parse_mode="HTML")

def show_chats(update: Update, context: CallbackContext):
    """Owner command to show the main menu for chat management."""
    if update.message and update.message.from_user.id == OWNER_ID:
        update.message.reply_text("Choose chat type to browse:", reply_markup=get_main_menu_buttons())

# === Callback Handler for Chat Management Menu ===
def show_chats_callback(update: Update, context: CallbackContext):
    """Handles inline button presses for chat browsing and pagination."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        query.answer("You are not the bot owner. 🚫")
        return
        
    query.answer()
    data = query.data
    
    if data == "show_main_menu":
        # Back to main menu
        query.edit_message_text("Choose chat type to browse:", reply_markup=get_main_menu_buttons())
        return

    try:
        # Expected data format: show_personal_0 or show_groups_1
        parts = data.split("_")
        chat_type = parts[1] # 'personal' or 'groups'
        page = int(parts[2])
    except (IndexError, ValueError):
        logging.error(f"Invalid callback data for show_chats: {data}")
        query.edit_message_text("❌ Oops! Something went wrong with the menu.")
        return

    # Determine display title
    title = "Personal Chats" if chat_type == "personal" else "Group Chats"
    
    # Fetch buttons and stats
    reply_markup, total_chats, skip, count_on_page, total_pages = get_chat_list_buttons(chat_type, page)
    
    # Build the message text
    if total_chats == 0:
        text = f"💖 {title} 💖\n\nNo chats of this type found in the database yet."
    else:
        text = (
            f"💖 {title} 💖\n\n"
            f"Total chats: **{total_chats}**\n"
            f"Showing: **{skip + 1}** to **{skip + count_on_page}** of **{total_chats}**\n"
            f"Page **{page + 1}** of **{total_pages}**"
        )

    # Edit the message to show the list of chats
    try:
        # We need to escape text for MarkdownV2 before sending
        text_safe = escape_markdown(text, version=2)
        query.edit_message_text(
            text=text_safe, 
            reply_markup=reply_markup, 
            parse_mode="MarkdownV2"
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logging.error(f"Failed to edit chat list message: {e}")
            query.edit_message_text(f"❌ Error updating message: {e}")

# === Group Tracking ===
def track_bot_added_removed(update: Update, context: CallbackContext):
    cmu = update.my_chat_member
    if not cmu or cmu.new_chat_member.user.id != context.bot.id:
        return
    user, chat = cmu.from_user, cmu.chat
    if cmu.old_chat_member.status in ["left", "kicked"] and cmu.new_chat_member.status in ["member", "administrator"]:
        msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> added Mitsuri to <b>{escape(chat.title)}</b>."
        save_chat_info(chat.id, user=user, chat=chat)
    elif cmu.new_chat_member.status in ["left", "kicked"]:
        msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> removed Mitsuri from <b>{escape(chat.title)}</b>."
    else:
        return
    try:
        context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")
    except BadRequest as e:
        logging.warning(f"Group event log failed: {e}")

# === Message Handling ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    chat_id = chat.id
    chat_type = chat.type
    chosen_name = (user.first_name or user.username or "User")[:25]

    # group triggers
    if chat_type in ["group", "supergroup"]:
        lower_text = user_input.lower()
        mentioned = (
            re.search(r"\bmitsuri\b", lower_text)
            or (context.bot.username and f"@{context.bot.username.lower()}" in lower_text)
            or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        )
        if not mentioned:
            return
        user_input = re.sub(rf"@{re.escape(context.bot.username)}", "", user_input, flags=re.I)
        user_input = re.sub(r"(?i)\bmitsuri\b", "", user_input).strip()
        if not user_input:
            user_input = "hi"
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 5:
            return
        GROUP_COOLDOWN[chat_id] = now

    save_chat_info(chat_id, user=user, chat=chat)
    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    if len(history) > 6:
        history = history[-6:]

    prompt = build_prompt(history, user_input, chosen_name)
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_text(update, reply)

# === Error Handling ===
def error_handler(update: object, context: CallbackContext):
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")

# === MAIN ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("eval", eval_command, filters=Filters.user(user_id=OWNER_ID)))
    dp.add_handler(CommandHandler("show", show_chats))

    # === NEW HANDLER FOR INLINE KEYBOARD CALLS ===
    dp.add_handler(CallbackQueryHandler(show_chats_callback, pattern=r"show_(personal|groups)_\d+|show_main_menu"))

    dp.add_handler(MessageHandler((Filters.text & ~Filters.command), handle_message))
    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()
