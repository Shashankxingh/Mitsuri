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
    ChatMemberHandler,
    CallbackQueryHandler,
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

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

REQUEST_DELAY = 10
BOT_START_TIME = time.time()

# In-memory chat history storage (for demo)
# Format: {chat_id: [("user", msg), ("bot", reply), ...]} max 10 entries to keep last 5 exchanges
chat_histories = {}


def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)


def get_all_chat_ids():
    return [chat["chat_id"] for chat in chat_info_collection.find()]


def send_typing(update: Update, context: CallbackContext):
    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")


def build_prompt(chat_id, user_input, chosen_name):
    # Load last 5 messages (up to 10 items = 5 user+5 bot msgs)
    history = chat_histories.get(chat_id, [])
    # We'll include last 5 user+bot exchanges (max 10 messages), keep only last 5 user messages

   def build_prompt(history, user_input, chosen_name):
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

    # Add character examples for tone
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
"""

    prompt = system_instructions.strip() + "\n\n"

    # Add previous chat history formatted as conversation
    for role, msg in history[-10:]:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        else:
            prompt += f"Faiza: {msg}\n"

    # Add current user input
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


def show_chats(update: Update, context: CallbackContext):
    if (
        update.message
        and update.message.from_user.id == OWNER_ID
        and update.message.chat_id == SPECIAL_GROUP_ID
    ):
        buttons = [
            [
                InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0"),
                InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0"),
            ]
        ]
        update.message.reply_text("Choose what to show:", reply_markup=InlineKeyboardMarkup(buttons))


def show_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "back_to_menu":
        buttons = [
            [
                InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0"),
                InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0"),
            ]
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
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([buttons]))

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
        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([buttons]))


def track_bot_added_removed(update: Update, context: CallbackContext):
    cmu = update.my_chat_member
    if cmu and cmu.new_chat_member.user.id == context.bot.id:
        old = cmu.old_chat_member.status
        new = cmu.new_chat_member.status
        user = cmu.from_user
        chat = cmu.chat
        if old in ["left", "kicked"] and new in ["member", "administrator"]:
            msg = f"<a href='tg://user?id={user.id}'>{user.first_name}</a> added me to <b>{chat.title}</b>."
            save_chat_info(chat.id, user=user, chat=chat)
        elif new in ["left", "kicked"]:
            msg = f"<a href='tg://user?id={user.id}'>{user.first_name}</a> removed me from <b>{chat.title}</b>."
        else:
            return
        context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")


def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    # For groups, respond only if "faiza" is mentioned or bot is replied to
    if chat_type in ["group", "supergroup"]:
        is_reply = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        if not ("faiza" in user_input.lower() or is_reply):
            return
        if user_input.lower() == "faiza":
            safe_reply_text(update, "Haan? Bol?")
            return

    save_chat_info(chat_id, user=user, chat=update.message.chat)

    # Append user message to history
    chat_histories.setdefault(chat_id, []).append(("user", user_input))

    # Keep only last 10 messages (5 exchanges)
    chat_histories[chat_id] = chat_histories[chat_id][-10:]

    prompt = build_prompt(chat_id, user_input, chosen_name)
    send_typing(update, context)

    reply = generate_with_retry(prompt)

    # Append bot reply to history
    chat_histories[chat_id].append(("bot", reply))
    chat_histories[chat_id] = chat_histories[chat_id][-10:]

    safe_reply_text(update, reply)


def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("ping", ping))
    dispatcher.add_handler(CommandHandler("showchats", show_chats))
    dispatcher.add_handler(CallbackQueryHandler(show_callback))
    dispatcher.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()