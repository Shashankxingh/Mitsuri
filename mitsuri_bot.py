import os
import time
import datetime
import logging
import re
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
from pymongo import MongoClient
import google.generativeai as genai

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and group IDs ===
OWNER_ID = 8162412883
SPECIAL_GROUP_ID = -1002759296936  # ✅ Use full supergroup ID

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === MongoDB setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]
chat_info_collection.create_index("chat_id", unique=True)

# === Logging setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# === Constants ===
REQUEST_DELAY = 2
BOT_START_TIME = time.time()
GROUP_COOLDOWN = {}

# === Utility Functions ===
def get_main_menu_buttons():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0"),
        InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0"),
    ]])

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = """
- Tum Mitsuri Kanroji ho demon slayer wali
- tumhe Hinglish me baatein karni hai
- Tum cute, cringe ho, childish ho
- baaton ko 1 ya 2 line me rakhna usse jyada nahi
- don't use actions like *giggles*, *blush* etc. instead use emojis
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"{msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

            if response is None:
                return "I'm not sure how to respond to that right now."
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "Something went wrong..."
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "I'm having trouble responding at the moment."

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === Command Handlers ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hello. Mitsuri is here. How can I help you today?")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return
    user = update.message.from_user
    name = escape(user.first_name or user.username or "User")
    msg = update.message.reply_text("Checking latency...")

    try:
        start_api_time = time.time()
        gemini_reply = model.generate_content("Just say pong.").text.strip()
        api_latency = round((time.time() - start_api_time) * 1000)
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

def show_chats(update: Update, context: CallbackContext):
    if update.message and update.message.from_user.id == OWNER_ID and update.message.chat_id == SPECIAL_GROUP_ID:
        update.message.reply_text("Choose chat type:", reply_markup=get_main_menu_buttons())

# This helper function handles re-rendering the chat list
def _send_chat_list(query, chat_type_prefix, page):
    start = page * 10
    end = start + 10
    
    if chat_type_prefix == "show_personal":
        users = list(chat_info_collection.find({"chat_id": {"$gt": 0}}))
        selected = users[start:end]
        lines = [f"<b>👤 Personal Chats (Page {page + 1})</b>"]
        all_buttons = []
        for user in selected:
            uid = user.get("chat_id")
            name = escape(user.get("name", "Unknown"))
            user_id = user.get("user_id")
            link = f"<a href='tg://user?id={user_id}'>{name}</a>" if user_id else name
            lines.append(f"• {link}\n  ID: <code>{uid}</code>")
            all_buttons.append([
                InlineKeyboardButton(f"❌ Forget {name}", callback_data=f"forget_{uid}_{page}")
            ])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(users):
            nav_buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)
        
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))
    
    elif chat_type_prefix == "show_groups":
        groups = list(chat_info_collection.find({"chat_id": {"$lt": 0}}))
        selected = groups[start:end]
        lines = [f"<b>👥 Group Chats (Page {page + 1})</b>"]
        all_buttons = []
        for group in selected:
            gid = group.get("chat_id")
            title = escape(group.get("title", "Unnamed"))
            adder_id = group.get("user_id")
            adder_name = escape(group.get("name", "Unknown"))
            adder_link = f"<a href='tg://user?id={adder_id}'>{adder_name}</a>" if adder_id else adder_name
            
            # === Corrected "Open Group" link logic ===
            group_link_str = "N/A"
            if str(gid).startswith("-100"):
                short_gid = str(gid)[4:]
                group_link_str = f"https://t.me/c/{short_gid}"
            
            lines.append(
                f"• <b>{title}</b>\n"
                f"  ID: <code>{gid}</code>\n"
                f"  Added By: {adder_link}\n"
                f"  Link: <a href='{group_link_str}'>Open Group</a>"
            )
            all_buttons.append([
                InlineKeyboardButton(f"❌ Forget {title}", callback_data=f"forget_{gid}_{page}")
            ])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(groups):
            nav_buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)

        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))

def show_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "back_to_menu":
        return query.edit_message_text("Choose chat type:", reply_markup=get_main_menu_buttons())
    
    # === Improved 'forget' button logic ===
    if data.startswith("forget_"):
        parts = data.split("_")
        chat_id_to_delete = int(parts[1])
        page = int(parts[2])
        chat_info_collection.delete_one({"chat_id": chat_id_to_delete})
        
        # Determine the current chat type and re-render the list
        chat_type_prefix = "show_groups" if chat_id_to_delete < 0 else "show_personal"
        query.answer("Chat has been forgotten.")
        _send_chat_list(query, chat_type_prefix, page)
        return
    
    page = int(data.split("_")[-1])
    
    if data.startswith("show_personal_"):
        _send_chat_list(query, "show_personal", page)
    elif data.startswith("show_groups_"):
        _send_chat_list(query, "show_groups", page)

def track_bot_added_removed(update: Update, context: CallbackContext):
    cmu = update.my_chat_member
    if cmu and cmu.new_chat_member.user.id == context.bot.id:
        old = cmu.old_chat_member.status
        new = cmu.new_chat_member.status
        user = cmu.from_user
        chat = cmu.chat
        if old in ["left", "kicked"] and new in ["member", "administrator"]:
            msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> added Mitsuri to <b>{escape(chat.title)}</b>."
            save_chat_info(chat.id, user=user, chat=chat)
        elif new in ["left", "kicked"]:
            msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> removed Mitsuri from <b>{escape(chat.title)}</b>."
        else:
            return
        try:
            context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")
        except BadRequest as e:
            logging.warning(f"Failed to log group event: {e}")

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    chat_id = chat.id
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    if chat_type in ["group", "supergroup"]:
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 5:
            return
        GROUP_COOLDOWN[chat_id] = now
        
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        
        if not (is_mention or is_reply):
            return
        if user_input.strip().lower() == context.bot.username.lower() or is_mention:
            safe_reply_text(update, "Yes?")
            return

    save_chat_info(chat_id, user=user, chat=chat)

    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    if len(history) > 6:
        history = history[-6:]
    prompt = build_prompt(history, user_input, chosen_name)

    # === Reverted to typing indicator ===
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_text(update, reply)

def error_handler(update: object, context: CallbackContext):
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")
    try:
        raise context.error
    except Unauthorized:
        logging.warning("Unauthorized")
    except BadRequest as e:
        logging.warning(f"BadRequest: {e}")
    except Exception as e:
        logging.error(f"Unhandled error: {e}")

# === Main ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("show", show_chats))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_handler(CallbackQueryHandler(show_callback))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()
