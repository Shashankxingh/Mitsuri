import os
import time
import datetime
import logging
import re
import sys
import io
from html import escape
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    CallbackQueryHandler,
    ChatMemberHandler
)
from telegram.error import Unauthorized, BadRequest
import google.generativeai as genai

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Owner and special group IDs ===
OWNER_ID = 8162412883
SPECIAL_GROUP_ID = -1002759296936  # Replace with your special group ID

# === Gemini AI config ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    "models/gemini-2.5-flash-lite",
    generation_config={"max_output_tokens": 150, "temperature": 0.9, "top_p": 0.95}
)

# === Logging ===
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# === Constants ===
REQUEST_DELAY = 2
BOT_START_TIME = time.time()
GROUP_COOLDOWN = {}

# === In-memory storage ===
CHAT_RECORDS = {}  # chat_id -> {type, title, username}

# === Utility Functions ===
def save_chat_record(chat):
    """Saves chat details to in-memory storage."""
    CHAT_RECORDS[chat.id] = {
        "type": chat.type,
        "title": getattr(chat, "title", chat.first_name),
        "username": getattr(chat, "username", None)
    }

def build_prompt(last_messages, user_input, chosen_name):
    """Builds the prompt for the Gemini API based on a ChatGPT persona."""
    system_instructions = """
You are ChatGPT, a highly knowledgeable and helpful AI assistant.
Your responses should be clear, concise, and polite.
Keep each response to 1-2 sentences.
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"ChatGPT: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nChatGPT:"
    return prompt

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    """Generates content from the Gemini model with a retry mechanism."""
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration,2)}s")

            response_text = None
            if hasattr(response, "text") and response.text:
                response_text = response.text
            elif hasattr(response, "candidates") and response.candidates:
                try:
                    response_text = response.candidates[0].content.parts[0].text.strip()
                except Exception:
                    pass

            if not response_text:
                response_text = "Sorry, I didn't understand that."

            # Limit to 1-2 lines for consistency
            response_text = "\n".join(response_text.splitlines()[:2])
            return response_text.strip()
        except Exception as e:
            logging.error(f"Gemini error attempt {attempt+1}: {e}")
            if attempt < retries-1:
                time.sleep(delay)
    return "I'm a bit busy now, please try again later."

def safe_reply_text(update, text):
    """Safely replies to a message, handling common exceptions."""
    try:
        update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest):
        pass

def format_uptime(seconds):
    """Formats a duration in seconds into a human-readable string."""
    return str(datetime.timedelta(seconds=int(seconds)))

# === /show Command from Code 1 ===
def get_main_menu_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0")],
        [InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
    ])

def _send_chat_list(query, chat_type_prefix, page):
    start = page * 10
    end = start + 10
    
    if chat_type_prefix == "show_personal":
        users = [(cid, info) for cid, info in CHAT_RECORDS.items() if info["type"] not in ["group", "supergroup"]]
        selected = users[start:end]
        lines = [f"<b>👤 Personal Chats (Page {page + 1})</b>"]
        all_buttons = []
        for chat_id, info in selected:
            name = escape(info["title"])
            link = f"tg://user?id={chat_id}"
            lines.append(f"• {name}\n  ID: <code>{chat_id}</code>")
            all_buttons.append([InlineKeyboardButton(f"❌ Forget {name}", callback_data=f"forget_{chat_id}_{page}")])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(users):
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)

        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))
    
    elif chat_type_prefix == "show_groups":
        groups = [(cid, info) for cid, info in CHAT_RECORDS.items() if info["type"] in ["group", "supergroup"]]
        selected = groups[start:end]
        lines = [f"<b>👥 Group Chats (Page {page + 1})</b>"]
        all_buttons = []
        for chat_id, info in selected:
            title = escape(info["title"])
            link = f"https://t.me/{info['username']}" if info["username"] else "N/A"
            lines.append(f"• <b>{title}</b>\n  ID: <code>{chat_id}</code>\n  Link: {link}")
            all_buttons.append([InlineKeyboardButton(f"❌ Forget {title}", callback_data=f"forget_{chat_id}_{page}")])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(groups):
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)

        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))

def show(update: Update, context: CallbackContext):
    """Handles the /show command using Code 1 style with pagination."""
    if update.message.from_user.id != OWNER_ID:
        safe_reply_text(update, "❌ Only owner can use this.")
        return
    update.message.reply_text("Choose chat type:", reply_markup=get_main_menu_buttons())

def show_callback(update: Update, context: CallbackContext):
    """Handles inline keyboard button presses for /show pagination."""
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "back_to_menu":
        return query.edit_message_text("Choose chat type:", reply_markup=get_main_menu_buttons())
    
    if data.startswith("forget_"):
        parts = data.split("_")
        chat_id_to_delete = int(parts[1])
        page = int(parts[2])
        if chat_id_to_delete in CHAT_RECORDS:
            del CHAT_RECORDS[chat_id_to_delete]
            query.answer("Chat deleted successfully.")
        _send_chat_list(query, "show_groups" if CHAT_RECORDS.get(chat_id_to_delete, {}).get("type") in ["group", "supergroup"] else "show_personal", page)
        return
    
    page = int(data.split("_")[-1])
    if data.startswith("show_personal_"):
        _send_chat_list(query, "show_personal", page)
    elif data.startswith("show_groups_"):
        _send_chat_list(query, "show_groups", page)

# === Command Handlers ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hello! I am ChatGPT, your AI assistant. How can I help you today?")

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

        reply = (
            f"╭───[ 🌐 <b>ChatGPT Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Uptime: <b>{uptime}</b>\n"
            f"╰─ I am online and responsive."
        )

        context.bot.edit_message_text(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            text=reply,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Something went wrong while checking ping.")

def eval_code(update: Update, context: CallbackContext):
    if update.message.from_user.id != OWNER_ID:
        update.message.reply_text("❌ Not allowed.")
        return
    code = " ".join(context.args)
    output = io.StringIO()
    try:
        sys.stdout = output
        exec(code, {})
        sys.stdout = sys.__stdout__
        result = output.getvalue()
        if not result:
            result = "<i>No output</i>"
        update.message.reply_text(f"✅ Result:\n<pre>{escape(result)}</pre>", parse_mode="HTML")
    except Exception as e:
        sys.stdout = sys.__stdout__
        update.message.reply_text(f"❌ Error:\n<pre>{escape(str(e))}</pre>", parse_mode="HTML")

# === AI Chat Handler ===
def handle_message(update: Update, context: CallbackContext):
    chat = update.message.chat
    user = update.message.from_user
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username
    user_input = update.message.text

    if not user_input:
        return

    save_chat_record(chat)

    # Group mention logic
    if chat_type in ["group", "supergroup"]:
        now = time.time()
        if chat.id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat.id] < 5:
            return
        GROUP_COOLDOWN[chat.id] = now

        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        pattern = re.compile(r'\bChatGPT\b|\@chatgpt_bot', re.I)
        is_name_mentioned = pattern.search(user_input)
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

        if not (is_mention or is_name_mentioned or is_reply):
            return

        user_input = re.sub(r'@' + re.escape(context.bot.username), '', user_input, flags=re.I).strip()
        user_input = pattern.sub('', user_input).strip() or "Hello!"

    # Chat history
    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    if len(history) > 6:
        history = history[-6:]
    prompt = build_prompt(history, user_input, chosen_name)

    try:
        context.bot.send_chat_action(chat_id=chat.id, action="typing")
    except Exception:
        pass

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_text(update, reply)

# === Bot-added notifications ===
def notify_bot_added(update: Update, context: CallbackContext):
    member_status = update.chat_member.new_chat_member.status
    user = update.chat_member.new_chat_member.user
    if user.id == context.bot.id and member_status == "member":
        chat = update.chat_member.chat
        context.bot.send_message(
            chat_id=SPECIAL_GROUP_ID,
            text=f"🌐 ChatGPT was added to <b>{chat.title}</b> ({chat.type})!",
            parse_mode="HTML"
        )
        save_chat_record(chat)

# === Callback Queries ===
def callback_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data

    if data.startswith("forget_") or data.startswith("show_") or data == "back_to_menu":
        show_callback(update, context)

# === Error handler ===
def error_handler(update: object, context: CallbackContext):
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")

# === Main ===
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("eval", eval_code, pass_args=True))
    dp.add_handler(CommandHandler("show", show))

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    dp.add_handler(ChatMemberHandler(notify_bot_added, ChatMemberHandler.CHAT_MEMBER))
    dp.add_handler(CallbackQueryHandler(callback_handler))

    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()