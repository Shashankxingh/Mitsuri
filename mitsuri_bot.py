import os
import time
import datetime
import logging
import re
from dotenv import load_dotenv
from html import escape
from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)
from telegram.error import Unauthorized, BadRequest
import google.generativeai as genai

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# === Owner ID ===
OWNER_ID = 8162412883

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    "models/gemini-2.5-flash-lite",
    generation_config={
        "max_output_tokens": 512,
        "temperature": 0.9,
        "top_p": 0.95,
    }
)

# === Logging setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# === Constants ===
REQUEST_DELAY = 2
BOT_START_TIME = time.time()
GROUP_COOLDOWN = {}

# === Utility Functions ===
def build_prompt(last_messages, user_input, chosen_name):
    system_instructions = """
- Tum Mitsuri Kanroji ho, Demon Slayer anime se.
- Tumhe Hinglish mein baat karni hai.
- Tum bohot cute, thodi cringe, aur childish personality rakhti ho.
- Response ko 1-3 lines se jyada me mat rakhna.
- Actions jaise *giggles* ya *blush* nahi, uske badle emojis use karo.
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
    """Gemini 2.5 Flash-Lite wrapper with retry."""
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

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

            return response_text.strip()

        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "Abhi main thoda busy hu... baad mein baat karte hain! 😊"

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === Command Handlers ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hello. Mitsuri is here. How can I help you today?")

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
    if update.message.from_user.id != OWNER_ID:
        update.message.reply_text("❌ Not allowed.")
        return

    code = " ".join(context.args)
    try:
        result = eval(code)
        update.message.reply_text(f"✅ Result:\n<pre>{escape(str(result))}</pre>", parse_mode="HTML")
    except Exception as e:
        update.message.reply_text(f"❌ Error:\n<pre>{escape(str(e))}</pre>", parse_mode="HTML")

def show(update: Update, context: CallbackContext):
    history = context.chat_data.get("history", [])
    if not history:
        update.message.reply_text("No history yet 🌸")
        return
    formatted = "\n".join([f"{'👤' if r=='user' else '🌸'} {m}" for r, m in history])
    update.message.reply_text(f"<b>History</b>\n\n{escape(formatted)}", parse_mode="HTML")

# === Conversation Handling ===
def handle_message(update: Update, context: CallbackContext):
    if not update.message:
        return

    chat_id = update.message.chat.id
    user = update.message.from_user
    chat_type = update.message.chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username
    user_input = update.message.text if update.message.text else None

    if update.message.sticker:
        user_input = "Sticker sent 🩷"

    if not user_input:
        return

    # Group mention handling
    if chat_type in ["group", "supergroup"]:
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 5:
            return
        GROUP_COOLDOWN[chat_id] = now

        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        mitsuri_pattern = re.compile(r'\b[Mm]itsuri\b|\@mitsuri_1bot', re.IGNORECASE)
        is_name_mentioned = mitsuri_pattern.search(user_input)
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

        if not (is_mention or is_name_mentioned or is_reply):
            return

        # Clean input
        user_input = re.sub(r'@' + re.escape(context.bot.username), '', user_input, flags=re.I).strip()
        user_input = mitsuri_pattern.sub('', user_input).strip() or "Hi Mitsuri!"

    # In-memory history
    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    if len(history) > 6:
        history = history[-6:]
    prompt = build_prompt(history, user_input, chosen_name)

    try:
        context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        pass

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
    safe_reply_text(update, reply)

def error_handler(update: object, context: CallbackContext):
    logging.error(f"Update: {update}")
    logging.error(f"Context error: {context.error}")

# === Main ===
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("show", show))
    dp.add_handler(CommandHandler("eval", eval_code, pass_args=True))

    # Conversation handler (text + stickers)
    dp.add_handler(MessageHandler(
        (Filters.text | Filters.sticker) & ~Filters.command,
        handle_message
    ))

    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()