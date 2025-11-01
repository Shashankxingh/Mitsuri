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
from pymongo import MongoClient
import google.generativeai as genai

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and group IDs ===
OWNER_ID = 8162412883
SPECIAL_GROUP_ID = -1002759296936

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-2.5-flash-lite")

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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0")],
        [InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
    ])

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
        if chat.username:
            data["chat_username"] = chat.username
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = """
- Tum Mitsuri Kanroji ho, Demon Slayer anime se.
- Tumhe Hinglish mein baat karni hai, jaise "Hello, kaise ho?"
- Tum bohot cute, thodi cringe, aur childish personality rakhti ho.
- Har baat ko ek ya do line mein hi bolna, zyada lamba nahi.
- Actions jaise *giggles* ya *blush* nahi. 
- Tumhara dost, owner, aur sab kuch [Shashank](https://t.me/shashankxingh) hai
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def search_web_fallback(query):
    """Fetch concise real-time info when Gemini can't answer."""
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_redirect": 1, "no_html": 1}
        res = requests.get(url, params=params, timeout=8)
        data = res.json()
        if data.get("AbstractText"):
            return data["AbstractText"]
        elif data.get("RelatedTopics"):
            for t in data["RelatedTopics"]:
                if isinstance(t, dict) and t.get("Text"):
                    return t["Text"]
        return None
    except Exception as e:
        logging.error(f"Web search failed: {e}")
        return None

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

            response_text = getattr(response, "text", None)
            if response_text and response_text.strip():
                return response_text.strip()

            # fallback: web search if Gemini returns nothing
            query = prompt.split("Human")[-1].split(":")[-1].strip()[:150]
            web_info = search_web_fallback(query)
            if web_info:
                return f"Umm, I just checked 🌐 — {web_info}"
            return "Mujhe abhi exact info nahi mili 🥺"
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Abhi main thoda busy hu... baad mein baat karte hain! 😊"

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
    if update.message and update.message.from_user.id == OWNER_ID:
        update.message.reply_text("Choose chat type:", reply_markup=get_main_menu_buttons())

# === Group Event Tracker ===
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

# === Mitsuri Greeting ===
def mitsuri_hi(update: Update, context: CallbackContext):
    if update.message and update.message.text.strip().lower() == "mitsuri":
        update.message.reply_text("Hii!")

# === Main Message Handler ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    chat_id = chat.id
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    # Group logic
    if chat_type in ["group", "supergroup"]:
        lower_text = user_input.lower()
        mentioned = (
            "mitsuri" in lower_text
            or (context.bot.username and f"@{context.bot.username.lower()}" in lower_text)
            or (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        )
        if not mentioned:
            return

        user_input = re.sub(rf"@{re.escape(context.bot.username)}", "", user_input, flags=re.I)
        user_input = re.sub(r"\b[Mm]itsuri\b", "", user_input).strip()
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
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_text(update, reply)

# === Error Handler ===
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

    dp.add_handler(MessageHandler(Filters.regex(r"^[Mm]itsuri$") & Filters.chat_type.group, mitsuri_hi))
    dp.add_handler(MessageHandler(
        (Filters.text & ~Filters.command),
        handle_message
    ))

    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()