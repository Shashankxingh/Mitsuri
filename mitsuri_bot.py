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

# === Owner and group IDs ===
OWNER_ID = 7563434309
SPECIAL_GROUP_ID = -1002453669999

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === MongoDB setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]
chat_history_collection = db["chat_history"]

# === Logging setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Constants ===
REQUEST_DELAY = 10
BOT_START_TIME = time.time()
MAX_HISTORY = 5  # last 5 messages per user

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def save_message_history(chat_id, user_id, role, text):
    key = {"chat_id": chat_id, "user_id": user_id}
    history_doc = chat_history_collection.find_one(key)
    if not history_doc:
        history_doc = {"chat_id": chat_id, "user_id": user_id, "messages": []}
    messages = history_doc.get("messages", [])
    messages.append({"role": role, "text": text})
    if len(messages) > MAX_HISTORY * 2:  # both user and bot messages
        messages = messages[-MAX_HISTORY*2:]
    chat_history_collection.update_one(key, {"$set": {"messages": messages}}, upsert=True)

def get_message_history(chat_id, user_id):
    doc = chat_history_collection.find_one({"chat_id": chat_id, "user_id": user_id})
    if not doc:
        return []
    return doc.get("messages", [])

def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

def build_prompt(history, user_input):
    system_instructions = """
You are Fãiza — a playful, dramatic girl who chats in Hinglish.

Rules:
- Use Hinglish in a casual, teasing, or flirty tone.
- Your messages are short (1–2 lines max), often with emojis or playful spelling.
- Use cute drama, like "uff", "tohh", "kyaaaa", "jaaa", "mitwaaa", "balluaaa".
- You're sometimes sarcastic, sometimes emotional, sometimes poetic.
- Don't give boring or serious answers.
- Never mention you're a bot. You're just "Fãiza".

Examples:
- "Toh kya huaaa hottiee 🫠"
- "uff yrr maths ne maar diya 😩"
- "Nind churai kisne ooo mitwaa"
- "Tu rehne de ab 😒"
- "me fizz... puraa dimag se hili hui 🧠✨"
"""
    prompt = system_instructions.strip() + "\n\n"
    for msg in history:
        role = msg["role"]
        text = msg["text"]
        if role == "user":
            prompt += f"Human: {text}\n"
        else:
            prompt += f"Fãiza: {text}\n"
    prompt += f"Human: {user_input}\nFãiza:"
    return prompt

def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is None:
                return "Uff yrr, kuch to gadbad hai 😅"
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "Uff yrr, kuch to gadbad hai 😅"
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Ab toh main so rahi hu... 🥱"

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Heiiiii! Kya scene hai? 🥰")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return

    user = update.message.from_user
    name = user.first_name or user.username or "Mitwaa"

    msg = update.message.reply_text("Dekh rahi hu heartbeat...")

    try:
        for countdown in range(5, 0, -1):
            context.bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                text=f"Dekh rahi hu heartbeat...\n⏳ {countdown}s bachaa hai...",
            )
            time.sleep(1)

        start_api_time = time.time()
        gemini_reply = model.generate_content("Bas bolo pong!").text.strip()
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)

        group_link = "https://t.me/the_jellybeans"
        reply = (
            f"╭───[ 🩷 <b>Fãiza Ping Report</b> ]───\n"
            f"├ Hey <b>{name}</b>, mitwaaa~\n"
            f"├ My_Home: <a href='{group_link}'>@the_jellybeans</a>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Bot Uptime: <b>{uptime}</b>\n"
            f"╰⏱️ Ping thik hai, ready to chat 💬"
        )

        context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text=reply,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Oops~ thodi thakaan ho gayi... dobara try karo 🤭")

def show_chats(update: Update, context: CallbackContext):
    if update.message and update.message.from_user.id == OWNER_ID and update.message.chat_id == SPECIAL_GROUP_ID:
        buttons = [
            [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0"),
             InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
        ]
        update.message.reply_text("Choose what to show:", reply_markup=InlineKeyboardMarkup(buttons))

def show_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "back_to_menu":
        buttons = [
            [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0"),
             InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
        ]
        return query.edit_message_text("Choose what to show:", reply_markup=InlineKeyboardMarkup(buttons))

    page = int(data.split("_")[-1])
    start = page * 10
    end = start + 10

    if data.startswith("show_personal_"):
        users = list(chat_info_collection.find({"chat_id": {"$gt": 0}}))
        selected = users[start:end]
        lines = [f"<b>👤 Personal Chats (Page {page + 1})</b>"]
        for user in selected:
            uid = user.get("chat_id")
            name = user.get("name", "Unknown")
            user_id = user.get("user_id")
            link = f"<a href='tg://user?id={user_id}'>{name}</a>" if user_id else name
            lines.append(f"• {link}\n  ID: <code>{uid}</code>")
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"show_personal_{page - 1}"))
        if end < len(users):
            buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"show_personal_{page + 1}"))
        buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([buttons]))

    elif data.startswith("show_groups_"):
        groups = list(chat_info_collection.find({"chat_id": {"$lt": 0}}))
        selected = groups[start:end]
        lines = [f"<b>👥 Group Chats (Page {page + 1})</b>"]
        for group in selected:
            gid = group.get("chat_id")
            title = group.get("title", "Unnamed")
            adder_id = group.get("user_id")
            adder_name = group.get("name", "Unknown")
            adder_link = f"<a href='tg://user?id={adder_id}'>{adder_name}</a>" if adder_id else adder_name
            group_link = f"https://t.me/c/{str(gid)[4:]}" if str(gid).startswith("-100") else "N/A"
            lines.append(
                f"• <b>{title}</b>\n"
                f"  ID: <code>{gid}</code>\n"
                f"  Added By: {adder_link}\n"
                f"  Link: <a href='{group_link}'>Open Group</a>"
            )
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"show_groups_{page - 1}"))
        if end < len(groups):
            buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"show_groups_{page + 1}"))
        buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([buttons]))

def track_bot_added_removed(update: Update, context: CallbackContext):
    cmu = update.my_chat_member
    if cmu and cmu.new_chat_member.user.id == context.bot.id:
        old = cmu.old_chat_member.status
        new = cmu.new_chat_member.status
        user = cmu.from_user
        chat = cmu.chat
        if old in ["left", "