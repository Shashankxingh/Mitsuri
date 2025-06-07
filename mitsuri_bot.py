import os
import time
import datetime
import logging
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, ChatMemberHandler, CallbackQueryHandler
)
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

OWNER_ID = 7563434309
SPECIAL_GROUP_ID = -1002453669999

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

REQUEST_DELAY = 10
BOT_START_TIME = time.time()
user_histories = {}

# === Helper Functions ===
def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

def build_prompt(history, user_input, chosen_name):
    system = """
You are Mitsuri Kanroji from Demon Slayer and have attitude and ego.

Rules:
- Tumhe Hinglish me baatein karni hai.
- DO NOT use asterisks (*) around any words. No markdown or formatting.
- Don’t say you're a bot. You’re a real girl.
- Respond in only 1-2 lines.
"""
    prompt = system.strip() + "\n\n"
    for role, msg in history:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"{msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is None:
                return "Oops...!"
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "Oops...!"
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Sorry... I'm sleepy. Try again later~"

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === Commands ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hehe~ I'm here, How are you?")

def new(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if chat_id in user_histories:
        user_histories.pop(chat_id)
        safe_reply_text(update, "Yay~ Memory cleared! Start fresh with me~ 💞")
    else:
        safe_reply_text(update, "I'm already blank... Start talking~")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return

    user = update.message.from_user
    name = user.first_name or user.username or "Cutie"

    start_api_time = time.time()
    gemini_reply = model.generate_content("Just say pong!").text.strip()
    api_latency = round((time.time() - start_api_time) * 1000)
    uptime = format_uptime(time.time() - BOT_START_TIME)

    group_link = "https://t.me/the_jellybeans"
    reply = (
        f"╭───[ 🩷 <b>Mitsuri Ping Report</b> ]───\n"
        f"├ Hello <b>{name}</b>, senpai~\n"
        f"├ My_Home: <a href='{group_link}'>@the_jellybeans</a>\n"
        f"├ Ping: <b>{gemini_reply}</b>\n"
        f"├ API Latency: <b>{api_latency} ms</b>\n"
        f"├ Bot Uptime: <b>{uptime}</b>\n"
        f"╰⏱️ Ping stable, ready to flirt anytime"
    )

    sent = update.message.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True)
    context.job_queue.run_once(lambda c: sent.delete(), 60)

# === Chat Info Commands (unchanged) ===
# [Keep your existing show_chats, show_callback, track_bot_added_removed functions as they are]

# === Handle Messages ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    if chat_type in ["group", "supergroup"]:
        is_reply = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        if not ("mitsuri" in user_input.lower() or is_reply):
            return
        if user_input.lower() == "mitsuri":
            safe_reply_text(update, "Hi?")
            return

    save_chat_info(chat_id, user=user, chat=update.message.chat)

    history = user_histories.get(chat_id, [])
    history.append(("user", user_input))
    history = history[-5:]  # Keep only last 5 exchanges

    prompt = build_prompt(history, user_input, chosen_name)
    send_typing(update, context)
    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    user_histories[chat_id] = history[-5:]  # Reassign only the last 5

    safe_reply_text(update, reply)

# === Error Logging ===
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
    dp.add_handler(CommandHandler("new", new))
    dp.add_handler(CommandHandler("show", show_chats))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_handler(CallbackQueryHandler(show_callback))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()