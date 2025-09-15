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
    CHAT_RECORDS[chat.id] = {
        "type": chat.type,
        "title": getattr(chat, "title", chat.first_name),
        "username": getattr(chat, "username", None)
    }

def build_prompt(last_messages, user_input, chosen_name):
    system_instructions = """
You are Mickey Mouse from Disney.
Talk in a playful, cheerful, and fun tone, using silly or excited words sometimes.
Keep answers short (1–2 sentences), like you're chatting with friends.
Say things like "gosh!", "gee!", or "ha-ha!" sometimes.
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mickey Mouse: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMickey Mouse:"
    return prompt

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
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
                response_text = "Gosh! I didn’t catch that, pal!"

            response_text = ". ".join(response_text.split(".")[:2]).strip()
            return response_text
        except Exception as e:
            logging.error(f"Gemini error attempt {attempt+1}: {e}")
            if attempt < retries-1:
                time.sleep(delay)
    return "Ha-ha! I’m kinda busy right now, pal. Try again later!"

def safe_reply_text(update, text):
    try:
        if update.message:
            update.message.reply_text(text, parse_mode="HTML")
    except (Unauthorized, BadRequest):
        pass
    except Exception as e:
        logging.warning(f"Reply failed: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

# === /show ===
# <-- NOT TOUCHING THIS SECTION PER YOUR REQUEST -->

# === Command Handlers ===
def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Ha-ha! Mickey’s here, pal! Need a hand?")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return
    user = update.message.from_user
    name = escape(user.first_name or user.username or "Pal")
    msg = update.message.reply_text("Checking my ears... ha-ha!")

    try:
        start_api_time = time.time()
        resp = model.generate_content("Just say 'Pong, pal! Ha-ha!'")
        gemini_reply = getattr(resp, "text", None) or "Pong, pal! Ha-ha!"
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)

        reply = (
            f"╭───[ 🎩 <b>Mickey Mouse Status Report</b> ]───\n"
            f"├ Hey <b>{name}</b>! \n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Uptime: <b>{uptime}</b>\n"
            f"╰─ Gosh! Still kicking, pal! Ha-ha!"
        )

        # safer edit: only try if we can edit bot's own message
        try:
            context.bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                text=reply,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            # fallback: just send reply as new message
            update.message.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Gosh! Something went wrong, pal!")

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
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username or "Pal"
    user_input = update.message.text

    if not user_input:
        return

    save_chat_record(chat)

    if chat_type in ["group", "supergroup"]:
        now = time.time()
        if chat.id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat.id] < 5:
            return
        GROUP_COOLDOWN[chat.id] = now

        mention_pattern = re.compile(r'@lynx_aibot', re.I)
        is_mention = mention_pattern.search(user_input)
        name_pattern = re.compile(r'\bMickey(?: Mouse)?\b', re.I)
        is_name_mentioned = name_pattern.search(user_input)
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

        if not (is_mention or is_name_mentioned or is_reply):
            return

        user_input = mention_pattern.sub('', user_input)
        user_input = name_pattern.sub('', user_input)
        user_input = user_input.strip() or "Ha-ha! What's up, pal?"

    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    history[:] = history[-6:]  # trim
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
            text=f"🎩 Mickey just showed up in <b>{chat.title}</b> ({chat.type})! Ha-ha!",
            parse_mode="HTML"
        )
        save_chat_record(chat)

# === Callback Queries ===
def callback_handler(update: Update, context: CallbackContext):
    data = update.callback_query.data
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