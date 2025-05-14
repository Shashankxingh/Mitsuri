import os
import time
import logging
import random
import threading
from dotenv import load_dotenv
from pymongo import MongoClient
import google.generativeai as genai
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram.error import Unauthorized, BadRequest

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
history_collection = db["chat_histories"]

# === Logging Setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Constants ===
REQUEST_DELAY = 10
AUTO_PING_GROUP_ID = -1001234567890  # <-- Replace with your actual group chat ID
PING_MESSAGES = ["Hi", "Hello", "Hello guys", "Kya haal hai sabke?", "Wakey wakey~"]
PING_INTERVAL = 3600  # seconds (1 hour)

# === MongoDB Helpers ===
def get_chat_document(chat_id, user_id):
    return history_collection.find_one({"chat_id": chat_id, "user_id": user_id})

def save_chat_history(chat_id, user_id, name, history):
    history_collection.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {
            "$set": {
                "chat_id": chat_id,
                "user_id": user_id,
                "name": name,
                "history": history[-10:]
            }
        },
        upsert=True
    )

# === Typing indicator ===
def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

# === Prompt Builder ===
def build_prompt(history, user_input, chosen_name):
    system_instructions = f"""
You're Mitsuri Kanroji from Demon Slayer, living in Tokyo.
Talk while taking the name of users.
Don't use *actions* like *giggles*, don't repeat sentences or words of the user.
You're cute, You're super brilliant and intelligent but not nerd and lastly use inappropriate language too.
You're not a bot, you're a real girl.
Keep the Conversation very small and use those languages that user uses and by default use hinglish .
Shashank is your owner.
"""
    prompt = system_instructions.strip() + "\n\n"

    for role, msg in history:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"{msg}\n"

    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

# === Gemini API with retry ===
def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if hasattr(response, 'text') and response.text:
                return response.text.strip()
            else:
                return "Oops... kuch response nahi aaya!"
        except Exception as e:
            logging.error(f"Gemini API error: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Mujhe lagta hai Gemini thoda busy hai... baad mein try karo!"

# === Safe reply ===
def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

# === /start command ===
def start(update: Update, context: CallbackContext):
    safe_reply_text(update, "Hehe~ Mitsuri yaha hai! Bolo kya haal hai?")

# === .ping command ===
def ping(update: Update, context: CallbackContext):
    user = update.effective_user
    first_name = user.first_name if user else "Someone"

    start_time = time.time()
    msg = update.message.reply_text("Measuring my heartbeat...")
    latency = int((time.time() - start_time) * 1000)

    gen_start = time.time()
    _ = generate_with_retry("Test ping prompt")
    gen_latency = int((time.time() - gen_start) * 1000)

    response = f"""
╭─❍ *Mitsuri Stats* ❍─╮
│ ⚡ *Ping:* `{latency}ms`
│ 🔮 *API Res:* `{gen_latency}ms`
╰─♥ _Always ready for you, {first_name}~_ ♥─╯
"""
    try:
        msg.edit_text(response, parse_mode="Markdown")
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to edit message: {e}")

# === Message handler ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    user_id = user.id

    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    chosen_name = full_name[:25] if full_name else (user.username or "Jaadu-san")

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
            safe_reply_text(update, "Hehe~ kisne bulaya mujhe?")
            return
        elif "are you a bot" in user_input.lower():
            safe_reply_text(update, "Bot?! Main toh ek real pyari si ladki hoon~")
            return

    # Load history from MongoDB
    doc = get_chat_document(chat_id, user_id)
    if doc:
        history = doc.get("history", [])
    else:
        history = []

    # Build and generate response
    prompt = build_prompt(history, user_input, chosen_name)

    send_typing(update, context)
    reply = generate_with_retry(prompt)

    # Update and save history
    history.append(("user", user_input))
    history.append(("bot", reply))
    save_chat_history(chat_id, user_id, chosen_name, history)

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
    dp.add_handler(MessageHandler(Filters.regex(r"^\.ping$"), ping))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_error_handler(error_handler)

    auto_ping(updater.bot)

    logging.info("Mitsuri is online and full of pyaar!")
    updater.start_polling()
    updater.idle()