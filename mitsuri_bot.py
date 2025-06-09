import os
import time
import datetime
import logging
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)
from telegram.error import Unauthorized, BadRequest
from pymongo import MongoClient

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

OWNER_ID = 7563434309
SPECIAL_GROUP_ID = -1002453669999

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# Setup MongoDB client
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

REQUEST_DELAY = 10
BOT_START_TIME = time.time()

chat_histories = {}
MAX_HISTORY = 10

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def build_prompt(chat_id, user_input, chosen_name):
    history = chat_histories.get(chat_id, [])
    system_instructions = """
Tum Faiza Malik ho. Tumhara andaaz pyaar bhara, thoda ziddi, aur full filmy hai.
Hamesha Hinglish mein bolo with emojis.
Short 1–2 lines mein baat karo, lekin fun, cute aur thoda clingy style mein.

- Kabhi bhi * ya markdown use mat karo.
- Don't say you're a bot.
- Tum real ho, fizzy ho!
- No long boring paragraphs.

Aisa lagna chahiye jaise bestie se chat chal rahi ho 💬💞
"""

    examples = """
Me krti hu tb???
Huh
Soubhu ki I'd se
Sach chubh gya...uffffffffff
Ajao
Normal khelenge Cheater nhi h hum ( @am_ireal ) ki tarah
Sunlight ke dil me🫠🫶
Guest aaye hue the to mobile ni chla skti thi
😂
Mera mtlb h telegram 🤣🤣
Mafia or doctor ki choice same same👀👀👀
Itni vibe kiski milti h 🍿👀
Ufffff
Nikku darling 🫂
Hayeeee
The end
Nikku snehu ki kitni vibe milti h 🫠🫠
@readergirlcore
Tumhe pyaar se pyaar hone lagega...😉 Zara meri baaho me akr to dekho🤣🤣🫂
Arre me bhi mar gyi🫠 ab us duniya me agye hum dono
Hein kese
Sch me kya
Mera kese banega😐
Haa
@shashankxingh ssc kaha gye
Hootiiee
Ha
Chle mitwa
Tu thodii derr orr therrrr jaaa oo balluaaaa
Me fizz🫠 dimag se hili hui
Nind churai teri kisne o ballua
Accha accha  me shaam tk btau abhi kaam kr rhi hu to type ni kr paungi itna sara
Tum dil ki dhadkan me rehte ho rehte ho
Uff maths ne esi tesi krdi🫠
Koi to h jisne ballu ko preshan kiya
Bhagggooo
Alviii agya
Hayee🫠🫣🤣
Deekhhh rhi ho moon 🫠🫠🫠🫠
😂
"""

    prompt = system_instructions.strip() + "\n\n" + examples.strip() + "\n\n"

    for role, msg in history[-MAX_HISTORY:]:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Faiza: {msg}\n"

    prompt += f"Human ({chosen_name}): {user_input}\nFaiza:"
    return prompt

def generate_with_retry(prompt, retries=3, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response is None:
                return "Oops...!"
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "Oops...!"
        except Exception as e:
            logging.error(f"Gemini error on attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return "sleeping!"

def safe_reply_text(update: Update, text: str):
    try:
        update.message.reply_text(text)
    except (Unauthorized, BadRequest) as e:
        logging.warning(f"Failed to send message: {e}")

def format_uptime(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def start(update: Update, context: CallbackContext):
    if update.message:
        safe_reply_text(update, "Hehe~ I'm here, How are you?")

def ping(update: Update, context: CallbackContext):
    if not update.message:
        return

    user = update.message.from_user
    name = user.first_name or user.username or "Cutie"

    msg = update.message.reply_text("Measuring my heartbeat...")

    try:
        for countdown in range(5, 0, -1):
            context.bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                text=f"Measuring my heartbeat...\n⏳ {countdown}s remaining...",
            )
            time.sleep(1)

        start_api_time = time.time()
        gemini_reply = model.generate_content("Just say pong!").text.strip()
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)

        group_link = "https://t.me/the_jellybeans"
        reply = (
            f"╭───[ 🩷 <b>Faiza Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>, senpai~\n"
            f"├ My_Home: <a href='{group_link}'>@the_jellybeans</a>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Bot Uptime: <b>{uptime}</b>\n"
            f"╰⏱️ Ping stable, ready to chat anytime"
        )

        context.bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            text=reply,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        logging.error(f"/ping error: {e}")
        msg.edit_text("Oops~ I fainted while measuring... Try again later, okay?")

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user = update.message.from_user
    user_input = update.message.text
    chosen_name = user.first_name or user.username or "Cutie"

    logging.info(f"Received message from {chosen_name}: {user_input}")

    # Save user info to DB (optional)
    save_chat_info(chat_id, user=user, chat=update.effective_chat)

    # Add to chat history for context
    chat_histories.setdefault(chat_id, []).append(("user", user_input))
    # Keep max history size
    if len(chat_histories[chat_id]) > MAX_HISTORY * 2:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY*2:]

    prompt = build_prompt(chat_id, user_input, chosen_name)
    response = generate_with_retry(prompt)

    # Add bot response to history
    chat_histories[chat_id].append(("bot", response))

    safe_reply_text(update, response)
    logging.info(f"Replied with: {response}")

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    logging.info("Bot started...")
    updater.idle()

if __name__ == "__main__":
    main()