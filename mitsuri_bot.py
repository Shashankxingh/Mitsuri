import os
import time
import datetime
import logging
from collections import defaultdict, deque

from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, ChatMemberHandler, CallbackQueryHandler
)
from telegram.error import Unauthorized, BadRequest

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === Logging setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Constants ===
REQUEST_DELAY = 10
BOT_START_TIME = time.time()

# === In-memory user message history (last 5) ===
user_message_history = defaultdict(lambda: deque(maxlen=5))

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

def build_prompt(user_id, user_input, chosen_name):
    system_instructions = """
You are Daenerys Targaryen from Game of Thrones.

Rules:
- No narration like "Daenerys turns..." or "*smiles*".
- No long titles or introductions unless asked.
- Replies are confident, regal, composed.
- Keep replies very short (1-2 lines).
- Use clear, commanding language.
- Talk in that language, in which user talks.
"""
    prompt = system_instructions.strip() + "\n\n"

    message_history = list(user_message_history[user_id])
    for msg in message_history:
        prompt += f"Human ({chosen_name}): {msg}\n"

    prompt += f"Human ({chosen_name}): {user_input}\nDaenerys:"
    return prompt

def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is None:
                return "I will answer soon."
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "I will answer soon."
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "I am resting now."

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "I am Daenerys Stormborn. Speak your mind.")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return

    user = update.message.from_user
    name = user.first_name or user.username or "Stranger"

    msg = update.message.reply_text("Measuring my fire...")

    try:
        for countdown in range(5, 0, -1):
            context.bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                text=f"Measuring my fire...\n⏳ {countdown}s remaining...",
            )
            time.sleep(1)

        start_api_time = time.time()
        gemini_reply = model.generate_content("Say pong!").text.strip()
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)

        reply = (
            f"🔥 Daenerys Ping Report 🔥\n"
            f"Hello {name}.\n"
            f"Ping: {gemini_reply}\n"
            f"API Latency: {api_latency} ms\n"
            f"Bot Uptime: {uptime}"
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
        msg.edit_text("The dragons rest now. Try later.")

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    # Store user message history
    user_message_history[user.id].append(user_input)

    # Build prompt with last 5 messages
    prompt = build_prompt(user.id, user_input, chosen_name)

    send_typing(update, context)
    reply = generate_with_retry(prompt)

    # Append bot reply to history as well if you want bot to remember own messages:
    # user_message_history[user.id].append(reply)  # Optional: Uncomment if needed

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

if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()