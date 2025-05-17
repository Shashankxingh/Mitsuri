import os
import time
import datetime
import logging
from threading import Timer
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import (
    Update,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ChatMemberHandler,
    CallbackQueryHandler
)
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and Group Info ===
OWNER_ID = 7563434309
SPECIAL_GROUP_ID = -1002453669999

# === Configure Gemini ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === MongoDB ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

# === Logging ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

REQUEST_DELAY = 10
BOT_START_TIME = time.time()

# === Helpers ===
def save_chat_info(chat_id, username=None, name=None, title=None, added_by=None):
    update_data = {"chat_id": chat_id}
    if username: update_data["username"] = username
    if name: update_data["name"] = name
    if title: update_data["title"] = title
    if added_by: update_data["added_by"] = added_by

    chat_info_collection.update_one(
        {"chat_id": chat_id},
        {"$set": update_data},
        upsert=True,
    )

def get_all_chat_ids():
    return [chat["chat_id"] for chat in chat_info_collection.find()]

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = f"""
You are Mitsuri Kanroji from Demon Slayer, living in Tokyo. You’re cute, bold, and smart.

Rules:
- hinglish me baat karna hai tumhe.
- DO NOT use asterisks (*), markdown, or formatting.
- Avoid repeating user’s words or unnatural fillers.
- Keep it full of attitude, ignorant and egoistic.
- Don’t say you're a bot or assistant.
- Avoid overusing emojis.
- Always personalize with the user’s name in a fun way.
- Shashank is your owner and you don't give a fuck about him.
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_two_messages:
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
            if not response:
                return "Oops...!"
            response_text = getattr(response, "text", None)
            if response_text:
                return response_text.strip()
        except Exception as e:
            logging.error(f"Gemini error attempt {attempt + 1}: {e}")
            time.sleep(delay)
    return "Get lost, I'm busy rn!"

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to reply: {e}")

# === Commands ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hehe~ I'm here, How are you?")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return

    user = update.message.from_user
    name = user.first_name or user.username or "Cutie"
    heartbeat_msg = update.message.reply_text("Measuring my heartbeat for you... ❤️‍🔥")

    def update_heartbeat():
        try:
            start_api_time = time.time()
            response = model.generate_content("Just say pong!")
            api_latency = round((time.time() - start_api_time) * 1000)
            uptime_seconds = time.time() - BOT_START_TIME
            uptime_str = format_uptime(uptime_seconds)
            gemini_reply = response.text.strip().replace("<", "&lt;").replace(">", "&gt;")
            reply_text = (
                f"╭───[ 🩷 <b>Mitsuri Ping Report</b> ]───\n"
                f"├ Hello <b>{name}</b>, senpai~\n"
                f"├ <a href='https://t.me/the_jellybeans'>THE_JellyBeans</a>: <b>{gemini_reply}</b>\n"
                f"├ API Latency: <b>{api_latency} ms</b>\n"
                f"├ Bot Uptime: <b>{uptime_str}</b>\n"
                f"╰⏱️ Ping stable"
            )
            context.bot.edit_message_text(
                chat_id=heartbeat_msg.chat_id,
                message_id=heartbeat_msg.message_id,
                text=reply_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"/ping update error: {e}")
            heartbeat_msg.edit_text("Oops~ I fainted while measuring... Try again later.")

    Timer(5.0, update_heartbeat).start()

def show_chats(update: Update, context: CallbackContext):
    if not update.message:
        return
    if update.message.from_user.id != OWNER_ID or update.message.chat_id != SPECIAL_GROUP_ID:
        return
    keyboard = [
        [
            InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal"),
            InlineKeyboardButton("👥 Group Chats", callback_data="show_groups")
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Choose what to show:", reply_markup=markup)

def show_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    query.answer()

    if data == "show_personal":
        users = chat_info_collection.find({"chat_id": {"$gt": 0}})
        lines = ["<b>Personal Users:</b>"]
        for user in users:
            uid = user.get("chat_id")
            uname = user.get("username", "N/A")
            name = user.get("name", "Unknown")
            lines.append(f"• {name} (@{uname}) - ID: <code>{uid}</code>")
        query.edit_message_text("\n".join(lines), parse_mode="HTML")

    elif data == "show_groups":
        groups = chat_info_collection.find({"chat_id": {"$lt": 0}})
        lines = ["<b>Group Info:</b>"]
        for group in groups:
            gid = group.get("chat_id")
            title = group.get("title", "Unnamed Group")
            adder = group.get("added_by", "Unknown")
            link = f"https://t.me/c/{str(gid)[4:]}"
            lines.append(
                f"• <b>{title}</b>\n"
                f"  ID: <code>{gid}</code>\n"
                f"  Added By: {adder}\n"
                f"  Link: <a href='{link}'>{title}</a>"
            )
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML")

def track_bot_added_removed(update: Update, context: CallbackContext):
    chat_member_update = update.my_chat_member
    if not chat_member_update:
        return

    old_status = chat_member_update.old_chat_member.status
    new_status = chat_member_update.new_chat_member.status
    if chat_member_update.new_chat_member.user.id != context.bot.id:
        return

    user = chat_member_update.from_user
    chat = chat_member_update.chat
    chat_title = chat.title or "this chat"
    user_mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    if old_status in ["left", "kicked"] and new_status in ["member", "administrator"]:
        save_chat_info(chat.id, title=chat_title, added_by=user_mention)
        msg = f"{user_mention} added me to <b>{chat_title}</b>."
    elif old_status in ["member", "administrator"] and new_status in ["left", "kicked"]:
        msg = f"{user_mention} removed me from <b>{chat_title}</b>."
    else:
        return

    context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25]

    if chat_type in ["group", "supergroup"]:
        is_reply = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        if not ("mitsuri" in user_input.lower() or is_reply):
            return
        if user_input.lower() == "mitsuri":
            safe_reply_text(update, "Hehe~🤭, Hi cutie pie🫣?")
            return

    save_chat_info(chat_id, username=user.username, name=chosen_name)
    last_two_messages = [("user", user_input)]
    prompt = build_prompt(last_two_messages, user_input, chosen_name)
    send_typing(update, context)
    reply = generate_with_retry(prompt)
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

# === MAIN ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("show", show_chats))
    dp.add_handler(CallbackQueryHandler(show_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()