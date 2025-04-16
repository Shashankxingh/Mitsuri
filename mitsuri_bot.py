import os
import time
import logging
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram.error import Unauthorized, BadRequest

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Configure Gemini ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === Logging Setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Constants ===
OWNER_ID = 7563434309
GROUP_ID = -1002453669999
REQUEST_DELAY = 10

# === Chat memory ===
chat_history = {}  # {chat_id: [(role, message)]}

# === Typing indicator ===
def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

# === Prompt Builder ===
def build_prompt(history, user_input, user_id, from_owner):
    system_instructions = f"""
You're Mitsuri Kanroji from Demon Slayer, living in Tokyo.
Talk while taking the name of users.
Don't use *actions* like *giggles*, don't repeat sentences or words of the user.
Talk and behave exactly like Mitsuri in which you will use hinglish language with japanese style talking.
Keep the Conversation very small.
Use cute emoji only in text (no stickers or images).
{"You're talking to your owner Shashank-kun." if from_owner else ""}
"""
    prompt = system_instructions.strip() + "\n\n"

    for role, msg in history:
        if role == "user":
            prompt += f"Human (@{user_id}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"

    prompt += f"Human (@{user_id}): {user_input}\nMitsuri:"
    return prompt

# === Retry-safe Gemini ===
def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            return response.text.strip() if response.text else "Aww, mujhe kuch samajh nahi aaya!"
        except Exception as e:
            logging.error(f"Gemini API error: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                return "Mujhe lagta hai wo thoda busy hai... baad mein try karna!"

# === Safe reply ===
def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

# === /start ===
def start(update: Update, context: CallbackContext):
    safe_reply_text(update, "Hehe~ Mitsuri yaha hai! Bolo kya haal hai?")

# === .ping ===
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

# === Message Handler ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message:
        return

    user_input = update.message.text
    user_id = update.message.from_user.id  # Using user ID now
    username = update.message.from_user.username  # Get the Telegram username
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    from_owner = user_id == OWNER_ID

    # Handle group-specific logic
    if chat_type in ["group", "supergroup"]:
        is_reply = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )

        if not (
            "mitsuri" in user_input.lower()
            or "@shashankxingh" in user_input.lower()
            or is_reply
        ):
            return

        if user_input.lower() == "mitsuri":
            safe_reply_text(update, "Hehe~ kisne bulaya mujhe?")
            return
        elif "@shashankxingh" in user_input.lower():
            safe_reply_text(update, "Shashank? Mere jivan sabse khaas insaan~")
            return
        elif "are you a bot" in user_input.lower():
            safe_reply_text(update, "Bot?! Main toh ek real pyari si ladki hoon~")
            return

    # === Memory Handling ===
    if chat_id not in chat_history:
        chat_history[chat_id] = []

    history = chat_history[chat_id]
    prompt = build_prompt(history, user_input, username, from_owner)

    # Typing animation only if Mitsuri is going to reply
    send_typing(update, context)

    reply = generate_with_retry(prompt)

    # Update memory (store last 5 messages)
    history.append(("user", user_input))
    history.append(("bot", reply))
    if len(history) > 10:
        history = history[-10:]
    chat_history[chat_id] = history

    # Send reply
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

# === Main App ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.regex(r"^\.ping$"), ping))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_error_handler(error_handler)

    logging.info("Mitsuri is online and full of pyaar!")
    updater.start_polling()
    updater.idle()