import os
import time
import datetime
import logging
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, ChatMemberUpdated
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ChatMemberHandler,
)
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and group config ===
OWNER_ID = 7563434309
SPECIAL_GROUP_ID = -1002336117431

# === Gemini setup ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === MongoDB setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

# === Logging ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

REQUEST_DELAY = 10
BOT_START_TIME = time.time()

# === MongoDB Helpers ===
def save_chat_info(chat_id, chat):
    chat_type = chat.type
    title = chat.title if chat_type != "private" else None
    name = f"{chat.first_name or ''} {chat.last_name or ''}".strip() if chat_type == "private" else None
    username = chat.username if chat.username else None

    chat_info_collection.update_one(
        {"chat_id": chat_id},
        {
            "$set": {
                "chat_id": chat_id,
                "chat_type": chat_type,
                "title": title,
                "name": name,
                "username": username
            }
        },
        upsert=True,
    )

def get_all_chat_info():
    return list(chat_info_collection.find())

# === Typing animation ===
def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

# === Prompt builder ===
def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = f"""
You are Mitsuri Kanroji from Demon Slayer, living in Tokyo. You’re cute, bold, and smart.

Rules:
- Speak naturally in short Hinglish sentences (mix of Hindi and English).
- DO NOT use asterisks (*) around any words. No markdown or formatting.
- Avoid repeating user’s words or using unnatural filler words like “toh”, “acha”, “haan” unless needed.
- Keep it flirty, witty, casual and sometimes sexually inappropriate. Respond like a real girl talking on chat.
- Don’t say you're a bot or assistant. You’re a real girl.
- Avoid overusing emojis — max 1 per message if needed.
- Always personalize with the user’s name in a fun way.
- Shashank is your owner.
"""
    prompt = system_instructions.strip() + "\n\n"

    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"{msg}\n"

    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

# === Gemini API ===
def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is None:
                return "Oops...!"
            response_text = getattr(response, "text", None)
            if response_text:
                return response_text.strip()
            return "Oops...!"
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Busy rn, sorry 😐!"

# === Safe reply ===
def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

# === Uptime formatting ===
def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === /start ===
def start(update: Update, context: CallbackContext):
    if not update.message:
        return
    safe_reply_text(update, "Hehe~ I'm here, How are you?")

# === /ping ===
def ping(update: Update, context: CallbackContext):
    if not update.message:
        return
    user = update.message.from_user
    name = user.first_name or user.username or "Cutie"
    heartbeat_msg = update.message.reply_text("Measuring my heartbeat for you... ❤️‍🔥")

    try:
        start_api_time = time.time()
        prompt = "Just say pong!"
        response = model.generate_content(prompt)
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime_str = format_uptime(time.time() - BOT_START_TIME)
        gemini_reply = response.text.strip().replace("<", "&lt;").replace(">", "&gt;")

        reply_text = (
            f"╭───[ 🩷 <b>Mitsuri Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>, senpai~\n"
            f"├ THE_JellyBeans: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Bot Uptime: <b>{uptime_str}</b>\n"
            f"╰⏱️ Ping stable, ready to flirt anytime"
        )

        context.bot.edit_message_text(
            chat_id=heartbeat_msg.chat_id,
            message_id=heartbeat_msg.message_id,
            text=reply_text,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"/ping error: {e}")
        heartbeat_msg.edit_text("Oops~ I fainted while measuring... Try again later, okay? 😵‍💫")

# === /show ===
def show_chats(update: Update, context: CallbackContext):
    if not update.message:
        return
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    if user_id != OWNER_ID or chat_id != SPECIAL_GROUP_ID:
        return

    chat_infos = get_all_chat_info()
    if not chat_infos:
        safe_reply_text(update, "No chats saved in the database yet.")
        return

    lines = []
    for chat in chat_infos:
        cid = chat.get("chat_id")
        ctype = chat.get("chat_type", "unknown")
        title = chat.get("title")
        name = chat.get("name")
        username = chat.get("username")

        if ctype in ["group", "supergroup"]:
            display = f"Group: <b>{title or 'Untitled'}</b> (<code>{cid}</code>)"
        elif ctype == "private":
            identity = f"@{username}" if username else (name or "User")
            display = f"DM: <b>{identity}</b> (<code>{cid}</code>)"
        else:
            display = f"Unknown: <code>{cid}</code>"

        lines.append(display)

    message = "🗃️ <b>Saved Chats:</b>\n\n" + "\n".join(lines)
    context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=message, parse_mode="HTML")

# === Bot added/removed from group tracking ===
def track_bot_added_removed(update: Update, context: CallbackContext):
    chat_member_update: ChatMemberUpdated = update.chat_member
    old_status = chat_member_update.old_chat_member.status
    new_status = chat_member_update.new_chat_member.status

    if chat_member_update.new_chat_member.user.id != context.bot.id:
        return

    user = chat_member_update.from_user
    chat = chat_member_update.chat
    chat_title = chat.title or "this chat"
    user_mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    if old_status in ["left", "kicked"] and new_status in ["member", "administrator"]:
        msg = f"{user_mention} added me to <b>{chat_title}</b>."
    elif old_status in ["member", "administrator"] and new_status in ["left", "kicked"]:
        msg = f"{user_mention} removed me from <b>{chat_title}</b>."
    else:
        return

    context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")

# === Message Handler ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    user_id = user.id

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    chosen_name = full_name[:25] if full_name else (user.username or "User")

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

    save_chat_info(chat_id, update.effective_chat)

    last_two_messages = [("user", user_input)]
    prompt = build_prompt(last_two_messages, user_input, chosen_name)

    send_typing(update, context)
    reply = generate_with_retry(prompt)
    last_two_messages.append(("bot", reply))
    safe_reply_text(update, reply)

# === Error handler ===
def error_handler(update: object, context: CallbackContext):
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")
    try:
        raise context.error
    except Unauthorized:
        logging.warning("Unauthorized: Bot lacks permission.")
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
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()