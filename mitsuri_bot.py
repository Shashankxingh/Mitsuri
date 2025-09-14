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
SPECIAL_GROUP_ID = -1002759296936

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
- Actions jaise *giggles* ya *blush* nahi, uske badle emojis use karo.
- Koshish karna ki tumhari baaton mein thodi sweetness aur cuteness ho 🥰
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    """Robust wrapper for Gemini API."""
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

            if response is None:
                return "Mujhe samajh nahi aaya... 🥺"

            response_text = getattr(response, "text", None)
            if not response_text and hasattr(response, "candidates"):
                try:
                    response_text = response.candidates[0].text
                except Exception:
                    response_text = None

            return response_text.strip() if response_text else "Kuch gadbad ho gayi... 😞"
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
        resp = model.generate_content("Just say pong.")
        gemini_reply = getattr(resp, "text", None) or "pong"
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
            chat_id=msg.chat.id,   # fixed
            message_id=msg.message_id,
            text=reply,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Something went wrong while checking ping.")

# (show_chats, _send_chat_list, show_callback, track_bot_added_removed stay the same)

def mitsuri_hi(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return
    if update.message.chat.type in ["group", "supergroup"] and not update.message.text.startswith('/'):
        if update.message.text.strip().lower() == "mitsuri":
            update.message.reply_text("Hii!")

# (eval_command unchanged)

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    chat_id = chat.id
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    user_info = chat_info_collection.find_one({"user_id": user.id})
    if user_info and user_info.get("is_blocked"):
        logging.info(f"Ignoring message from blocked user {user.id}")
        return

    if chat_type in ["group", "supergroup"]:
        if user_input.lower() == "mitsuri":
            return
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 5:
            return
        GROUP_COOLDOWN[chat_id] = now

        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        mitsuri_pattern = re.compile(r'\b[Mm]itsuri\b')
        is_name_mentioned = mitsuri_pattern.search(user_input)

        if not (is_mention or is_reply or is_name_mentioned):
            return

        if is_mention:
            user_input = re.sub(r'@' + re.escape(context.bot.username), '', user_input, flags=re.I).strip()
        if is_name_mentioned:
            user_input = mitsuri_pattern.sub('', user_input).strip()
        if not user_input:
            return

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
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("show", show_chats))
    dp.add_handler(CommandHandler("eval", eval_command))

    dp.add_handler(MessageHandler(Filters.regex(r"^[Mm]itsuri$") & Filters.chat_type.group, mitsuri_hi))

    dp.add_handler(MessageHandler(
        (Filters.text & ~Filters.command & Filters.chat_type.group & (Filters.reply | Filters.entity("mention") | Filters.regex(r"\b[Mm]itsuri\b")))
        | (Filters.text & ~Filters.command & Filters.chat_type.private),
        handle_message
    ))

    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_handler(CallbackQueryHandler(show_callback))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()