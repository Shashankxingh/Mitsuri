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
SPECIAL_GROUP_ID = -1002759296936 # Replace with your special group ID

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
    """Builds the prompt for the Gemini API based on a character personality."""
    system_instructions = """
- Tum Mitsuri Kanroji ho, Demon Slayer anime se.
- Tumhe bahut knowledgeable bhi ho par in a cute way.
- Actions jaise *giggles* ya *blush* nahi.
- baaton ko 1 se 2 line me hi bolti ho.
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
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
                response_text = "Kuch gadbad ho gayi... 😞"

            # Limit to 1-2 lines for character consistency
            response_text = "\n".join(response_text.splitlines()[:2])
            return response_text.strip()
        except Exception as e:
            logging.error(f"Gemini error attempt {attempt+1}: {e}")
            if attempt < retries-1:
                time.sleep(delay)
    return "Abhi main thoda busy hu... baad mein baat karte hain! 😊"

def safe_reply_text(update, text):
    """Safely replies to a message, handling common exceptions."""
    try:
        update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest):
        pass

def format_uptime(seconds):
    """Formats a duration in seconds into a human-readable string."""
    return str(datetime.timedelta(seconds=int(seconds)))

# === Command Handlers ===
def start(update: Update, context: CallbackContext):
    """Handles the /start command."""
    if update.message:
        safe_reply_text(update, "Hello. Mitsuri is here. How can I help you today?")

def ping(update: Update, context: CallbackContext):
    """Handles the /ping command, showing bot and API latency."""
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
            f"╭───[ 🌸 <b>Mitsuri Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Uptime: <b>{uptime}</b>\n"
            f"╰─ I'm here and responsive."
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
    """Handles the /eval command for the owner, executing Python code safely."""
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

def show(update: Update, context: CallbackContext):
    """Handles the /show command, displaying a list of tracked chats with pagination."""
    if update.message.from_user.id != OWNER_ID:
        safe_reply_text(update, "❌ Only owner can use this.")
        return
    show_page(update, context, 0)

def show_page(update: Update, context: CallbackContext, page: int):
    """Renders a paginated view of chat records."""
    items_per_page = 10
    chats = list(CHAT_RECORDS.items())
    total_pages = (len(chats) - 1) // items_per_page + 1 if chats else 1
    page = max(0, min(page, total_pages - 1))

    start_index = page * items_per_page
    end_index = start_index + items_per_page
    keyboard = []

    for chat_id, info in chats[start_index:end_index]:
        title = escape(info["title"])
        link = f"https://t.me/{info['username']}" if info["username"] else f"tg://user?id={chat_id}"
        button_row = [
            InlineKeyboardButton(title, url=link),
            InlineKeyboardButton("Forget", callback_data=f"forget:{chat_id}")
        ]
        keyboard.append(button_row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ Prev", callback_data=f"page:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡", callback_data=f"page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    markup = InlineKeyboardMarkup(keyboard)

    text = f"📄 Chats & Groups (Page {page+1}/{total_pages}):"
    if update.callback_query:
        update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        update.message.reply_text(text, reply_markup=markup)

# === AI Chat Handler ===
def handle_message(update: Update, context: CallbackContext):
    """Handles all incoming messages and delegates them to the AI."""
    chat = update.message.chat
    user = update.message.from_user
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username
    user_input = update.message.text

    if not user_input:
        return

    save_chat_record(chat)

    # DM notification for owner
    if chat_type == "private":
        context.bot.send_message(
            chat_id=SPECIAL_GROUP_ID,
            text=f"🌸 <b>{user.first_name}</b> (@{user.username}) started a DM with Mitsuri!",
            parse_mode="HTML"
        )

    # Group mention logic
    if chat_type in ["group", "supergroup"]:
        now = time.time()
        if chat.id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat.id] < 5:
            return
        GROUP_COOLDOWN[chat.id] = now

        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        mitsuri_pattern = re.compile(r'\b[Mm]itsuri\b|\@mitsuri_1bot', re.I)
        is_name_mentioned = mitsuri_pattern.search(user_input)
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

        if not (is_mention or is_name_mentioned or is_reply):
            return

        user_input = re.sub(r'@' + re.escape(context.bot.username), '', user_input, flags=re.I).strip()
        user_input = mitsuri_pattern.sub('', user_input).strip() or "Hi Mitsuri!"

    # In-memory chat history
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
    """Notifies the owner when the bot is added to a new chat."""
    member_status = update.chat_member.new_chat_member.status
    user = update.chat_member.new_chat_member.user
    if user.id == context.bot.id and member_status == "member":
        chat = update.chat_member.chat
        context.bot.send_message(
            chat_id=SPECIAL_GROUP_ID,
            text=f"🌸 Mitsuri was added to <b>{chat.title}</b> ({chat.type})!",
            parse_mode="HTML"
        )
        save_chat_record(chat)

# === Callback Queries ===
def callback_handler(update: Update, context: CallbackContext):
    """Handles inline keyboard button presses."""
    query = update.callback_query
    data = query.data

    if data.startswith("forget:"):
        chat_id = int(data.split(":")[1])
        info = CHAT_RECORDS.get(chat_id)
        if not info:
            query.answer("❌ Chat not found.")
            return

        if info["type"] in ["group", "supergroup"]:
            try:
                context.bot.leave_chat(chat_id)
                del CHAT_RECORDS[chat_id]
                query.answer("Left the group successfully.")
            except Exception as e:
                query.answer(f"Error leaving group: {e}")
        else:
            del CHAT_RECORDS[chat_id]
            query.answer("Deleted chat successfully.")
        
        show_page(update, context, 0)

    elif data.startswith("page:"):
        page = int(data.split(":")[1])
        show_page(update, context, page)

def error_handler(update: object, context: CallbackContext):
    """Log all errors to the console."""
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")

# === Main ===
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Command Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("eval", eval_code, pass_args=True))
    dp.add_handler(CommandHandler("show", show))

    # Message Handler (only for text)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Bot-added notifications
    dp.add_handler(ChatMemberHandler(notify_bot_added, ChatMemberHandler.CHAT_MEMBER))

    # Callback queries
    dp.add_handler(CallbackQueryHandler(callback_handler))

    # Error handler
    dp.add_error_handler(error_handler)

    # Start the bot
    updater.start_polling()
    updater.idle()
