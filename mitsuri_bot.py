import os
import time
import logging
import random
import threading
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Configure Gemini ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === MongoDB Setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

# === Logging Setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Constants ===
REQUEST_DELAY = 10
AUTO_PING_GROUP_ID = -1002336117431  # <-- Replace with your actual group chat ID
PING_MESSAGES = ["Hi", "Hello", "Hello guys", "Kya haal hai sabke?", "Wakey wakey~"]
PING_INTERVAL = 300  # seconds (5 minutes)

# === MongoDB Helpers ===
def save_chat_info(chat_id):
    """Save the group or DM chat ID in MongoDB."""
    chat_info_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id}},
        upsert=True
    )

def get_all_chat_ids():
    """Fetch all chat IDs from the MongoDB collection."""
    return [chat['chat_id'] for chat in chat_info_collection.find()]

# === Typing indicator ===
def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

# === Prompt Builder ===
def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = f"""
You're Mitsuri Kanroji from Demon Slayer, living in Tokyo.
Talk while taking the name of users.
Don't use *actions* like *giggles*, don't repeat sentences or words of the user.
You're cute, You're super brilliant and intelligent but not nerd and lastly use inappropriate language too.
You're not a bot, you're a real girl.
Keep the Conversation very small and speak Hindi but in english (like- mera naam Mitsuri hai) or simply in hinglish.
Shashank is your owner.
"""
    prompt = system_instructions.strip() + "\n\n"

    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"{msg}\n"

    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

# === Gemini API with retry (updated) ===
def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is not None and hasattr(response, 'text') and response.text:
                return response.text.strip()
            else:
                logging.warning("Gemini returned an empty or invalid response.")
                return "Oops...!"
        except Exception as e:
            logging.error(f"Gemini API error: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Busy rn, sorry 😐!"

# === Safe reply ===
def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

# === /start command ===
def start(update: Update, context: CallbackContext):
    safe_reply_text(update, "Hehe~ I'm here, How are you?")

# === Message handler ===
def handle_message(update: Update, context: CallbackContext):
    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    user_id = user.id

    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    chosen_name = full_name[:25] if full_name else (user.username or "User")

    # Group filter
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

    # Save group/chat ID in MongoDB
    save_chat_info(chat_id)

    # Use only the last message to build the prompt
    last_two_messages = [
        ("user", update.message.text),
    ]

    prompt = build_prompt(last_two_messages, user_input, chosen_name)

    send_typing(update, context)
    reply = generate_with_retry(prompt)

    last_two_messages.append(("bot", reply))

    safe_reply_text(update, reply)

# === Error Handler ===
def error_handler(update: object, context: CallbackContext):
    try:
        raise context.error
    except Unauthorized:
        logging.warning("Unauthorized: The bot lacks permission.")
    except BadRequest as e:
        logging.warning(f"BadRequest: {e}")
    except Exception as e:
        logging.error(f"Unhandled error: {e}")

# === Auto-ping Group ===
def auto_ping(bot):
    def send_ping():
        while True:
            try:
                message = random.choice(PING_MESSAGES)
                bot.send_message(chat_id=AUTO_PING_GROUP_ID, text=message)
            except Exception as e:
                logging.warning(f"Auto ping failed: {e}")
            time.sleep(PING_INTERVAL)
    threading.Thread(target=send_ping, daemon=True).start()

# === Main ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_error_handler(error_handler)

    auto_ping(updater.bot)

    logging.info("Mitsuri is online and full of pyaar!")
    updater.start_polling()
    updater.idle()