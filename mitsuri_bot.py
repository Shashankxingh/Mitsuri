import os
import time
import datetime
import logging
import re
from dotenv import load_dotenv
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
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
import google.generativeai as genai

# === Load environment variables ===
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# === Owner and group IDs ===
OWNER_ID = 8162412883
SPECIAL_GROUP_ID = -1002759296936

# === Gemini configuration ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-2.5-flash-lite")

# === MongoDB setup ===
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["MitsuriDB"]
chat_info_collection = db["chat_info"]
chat_info_collection.create_index("chat_id", unique=True)

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
def get_main_menu_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Personal Chats", callback_data="show_personal_0")],
        [InlineKeyboardButton("👥 Group Chats", callback_data="show_groups_0")]
    ])

def save_chat_info(chat_id, user=None, chat=None):
    data = {"chat_id": chat_id}
    if user:
        data["name"] = user.first_name
        data["username"] = user.username
        data["user_id"] = user.id
    if chat and chat.type != "private":
        data["title"] = chat.title
        if chat.username:
            data["chat_username"] = chat.username
    chat_info_collection.update_one({"chat_id": chat_id}, {"$set": data}, upsert=True)

def build_prompt(last_two_messages, user_input, chosen_name):
    system_instructions = """
- Tum Mitsuri Kanroji ho, Demon Slayer anime se.
- Tumhe Hinglish mein baat karni hai, jaise "Hello, kaise ho?"
- Tum bohot cute, thodi cringe, aur childish personality rakhti ho.
- Har baat ko ek ya do line mein hi bolna, zyada lamba nahi.
- Actions jaise *giggles* ya *blush* nahi, uske badle emojis use karo.
- Koshish karna ki tumhari baaton mein thodi sweetness aur cuteness ho 🥰
"""
    prompt = system_instructions.strip() + "\n\n"
    for role, msg in last_two_messages:
        if role == "user":
            prompt += f"Human ({chosen_name}): {msg}\n"
        elif role == "bot":
            prompt += f"Mitsuri: {msg}\n"
    prompt += f"Human ({chosen_name}): {user_input}\nMitsuri:"
    return prompt

def generate_with_retry(prompt, retries=2, delay=REQUEST_DELAY):
    for attempt in range(retries):
        try:
            start = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start
            logging.info(f"Gemini response time: {round(duration, 2)}s")

            if response is None:
                return "Mujhe samajh nahi aaya... 🥺"
            response_text = getattr(response, "text", None)
            return response_text.strip() if response_text else "Kuch gadbad ho gayi... 😞"
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
        gemini_reply = model.generate_content("Just say pong.").text.strip()
        api_latency = round((time.time() - start_api_time) * 1000)
        uptime = format_uptime(time.time() - BOT_START_TIME)
        group_link = "https://t.me/mitsuri_homie"

        reply = (
            f"╭───[ 🌸 <b>Mitsuri Ping Report</b> ]───\n"
            f"├ Hello <b>{name}</b>\n"
            f"├ Group: <a href='{group_link}'>@the_jellybeans</a>\n"
            f"├ Ping: <b>{gemini_reply}</b>\n"
            f"├ API Latency: <b>{api_latency} ms</b>\n"
            f"├ Uptime: <b>{uptime}</b>\n"
            f"╰─ I'm here and responsive."
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
        msg.edit_text("Something went wrong while checking ping.")

def show_chats(update: Update, context: CallbackContext):
    if update.message and update.message.from_user.id == OWNER_ID:
        update.message.reply_text("Choose chat type:", reply_markup=get_main_menu_buttons())

def _send_chat_list(query, chat_type_prefix, page):
    start = page * 10
    end = start + 10
    
    if chat_type_prefix == "show_personal":
        users = list(chat_info_collection.find({"chat_id": {"$gt": 0}}))
        selected = users[start:end]
        lines = [f"<b>👤 Personal Chats (Page {page + 1})</b>"]
        all_buttons = []
        for user in selected:
            uid = user.get("chat_id")
            name = escape(user.get("name", "Unknown"))
            user_id = user.get("user_id")
            link = f"<a href='tg://user?id={user_id}'>{name}</a>" if user_id else name
            is_blocked = user.get("is_blocked", False)
            lines.append(f"• {link}\n  ID: <code>{uid}</code>")
            
            if is_blocked:
                all_buttons.append([InlineKeyboardButton(f"✅ Unblock {name}", callback_data=f"unblock_{uid}_{page}")])
            else:
                all_buttons.append([InlineKeyboardButton(f"🚫 Block {name}", callback_data=f"block_{uid}_{page}")])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(users):
            nav_buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)
        
        if users:
            all_buttons.append([InlineKeyboardButton("❌ Forget ALL Personal Chats", callback_data="forget_all_personal")])

        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))
    
    elif chat_type_prefix == "show_groups":
        groups = list(chat_info_collection.find({"chat_id": {"$lt": 0}}))
        selected = groups[start:end]
        lines = [f"<b>👥 Group Chats (Page {page + 1})</b>"]
        all_buttons = []
        for group in selected:
            gid = group.get("chat_id")
            title = escape(group.get("title", "Unnamed"))
            adder_id = group.get("user_id")
            adder_name = escape(group.get("name", "Unknown"))
            adder_link = f"<a href='tg://user?id={adder_id}'>{adder_name}</a>" if adder_id else adder_name
            
            group_link_str = "N/A"
            if group.get("chat_username"):
                group_link_str = f"https://t.me/{group['chat_username']}"
            elif str(gid).startswith("-100"):
                short_gid = str(gid)[4:]
                group_link_str = f"https://t.me/c/{short_gid}"
            
            lines.append(
                f"• <b>{title}</b>\n"
                f"  ID: <code>{gid}</code>\n"
                f"  Added By: {adder_link}\n"
                f"  Link: <a href='{group_link_str}'>Open Group</a>"
            )
            all_buttons.append([
                InlineKeyboardButton(f"❌ Forget {title}", callback_data=f"forget_{gid}_{page}")
            ])
        
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{chat_type_prefix}_{page - 1}"))
        if end < len(groups):
            nav_buttons.append(InlineKeyboardButton("▶️ Next", callback_data=f"{chat_type_prefix}_{page + 1}"))
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu"))
        all_buttons.append(nav_buttons)
        
        if groups:
            all_buttons.append([InlineKeyboardButton("❌ Forget ALL Group Chats", callback_data="forget_all_groups")])

        query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(all_buttons))

def show_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    
    if query.from_user.id != OWNER_ID:
        query.answer("You are not authorized to use this.")
        return

    query.answer()
    data = query.data

    if data == "back_to_menu":
        return query.edit_message_text("Choose chat type:", reply_markup=get_main_menu_buttons())
    
    if data.startswith("forget_"):
        parts = data.split("_")
        
        if parts[1] == "all":
            chat_type = parts[2]
            if chat_type == "personal":
                chat_info_collection.delete_many({"chat_id": {"$gt": 0}})
                query.edit_message_text("All personal chats have been forgotten.", parse_mode="HTML")
            elif chat_type == "groups":
                group_chats = list(chat_info_collection.find({"chat_id": {"$lt": 0}}))
                for group in group_chats:
                    try:
                        context.bot.leave_chat(chat_id=group["chat_id"])
                    except Exception as e:
                        logging.error(f"Failed to leave chat {group['chat_id']}: {e}")
                chat_info_collection.delete_many({"chat_id": {"$lt": 0}})
                query.edit_message_text("All group chats have been forgotten and left.", parse_mode="HTML")
            return
        
        chat_id_to_delete = int(parts[1])
        page = int(parts[2])

        if chat_id_to_delete < 0:
            try:
                context.bot.leave_chat(chat_id=chat_id_to_delete)
                query.answer("Chat has been forgotten and left.")
            except Exception as e:
                logging.error(f"Failed to leave chat {chat_id_to_delete}: {e}")
                query.answer("Failed to leave the chat, but data was forgotten.")
        else:
            query.answer("Chat has been forgotten.")
            
        chat_info_collection.delete_one({"chat_id": chat_id_to_delete})
        
        chat_type_prefix = "show_groups" if chat_id_to_delete < 0 else "show_personal"
        
        _send_chat_list(query, chat_type_prefix, page)
        return
    
    if data.startswith("block_") or data.startswith("unblock_"):
        parts = data.split("_")
        action = parts[0]
        chat_id_to_update = int(parts[1])
        page = int(parts[2])

        is_blocked = (action == "block")
        
        chat_info_collection.update_one({"chat_id": chat_id_to_update}, {"$set": {"is_blocked": is_blocked}})

        query.answer(f"User has been {'blocked' if is_blocked else 'unblocked'}.")
        _send_chat_list(query, "show_personal", page)
        return
    
    page = int(data.split("_")[-1])
    
    if data.startswith("show_personal_"):
        _send_chat_list(query, "show_personal", page)
    elif data.startswith("show_groups_"):
        _send_chat_list(query, "show_groups", page)

def track_bot_added_removed(update: Update, context: CallbackContext):
    cmu = update.my_chat_member
    if cmu and cmu.new_chat_member.user.id == context.bot.id:
        old = cmu.old_chat_member.status
        new = cmu.new_chat_member.status
        user = cmu.from_user
        chat = cmu.chat
        if old in ["left", "kicked"] and new in ["member", "administrator"]:
            msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> added Mitsuri to <b>{escape(chat.title)}</b>."
            save_chat_info(chat.id, user=user, chat=chat)
        elif new in ["left", "kicked"]:
            msg = f"<a href='tg://user?id={user.id}'>{escape(user.first_name)}</a> removed Mitsuri from <b>{escape(chat.title)}</b>."
        else:
            return
        try:
            context.bot.send_message(chat_id=SPECIAL_GROUP_ID, text=msg, parse_mode="HTML")
        except BadRequest as e:
            logging.warning(f"Failed to log group event: {e}")

def mitsuri_hi(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return
    
    if update.message.chat.type in ["group", "supergroup"] and not update.message.text.startswith('/'):
        message_text = update.message.text.strip().lower()
        if message_text == "mitsuri":
            update.message.reply_text("Hii!")

def eval_command(update: Update, context: CallbackContext):
    """Executes a Python expression provided by the owner."""
    if not update.message:
        return

    if update.message.from_user.id != OWNER_ID:
        update.message.reply_text("You are not authorized to use this command.")
        return

    if not context.args:
        update.message.reply_text("Please provide a code snippet to evaluate.")
        return

    code_to_eval = " ".join(context.args)

    try:
        locs = locals()
        globs = globals()

        import io
        import sys
        
        old_stdout = sys.stdout
        redirected_output = io.StringIO()
        sys.stdout = redirected_output

        exec(code_to_eval, globs, locs)
        
        sys.stdout = old_stdout
        output = redirected_output.getvalue()

        if not output and " " not in code_to_eval:
            output = str(eval(code_to_eval, globs, locs))
        
        if len(output) > 2000:
            output = output[:1900] + "\n... (output truncated)"
        
        reply_text = f"✅ <b>Output:</b>\n<pre>{escape(output)}</pre>" if output.strip() else "✅ <b>Success. No output.</b>"
        update.message.reply_text(reply_text, parse_mode="HTML")

    except Exception as e:
        error_text = f"❌ <b>Error:</b>\n<pre>{escape(str(e))}</pre>"
        update.message.reply_text(error_text, parse_mode="HTML")

def handle_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    user_input = update.message.text.strip()
    user = update.message.from_user
    chat = update.message.chat
    chat_id = chat.id
    chat_type = chat.type
    chosen_name = f"{user.first_name or ''} {user.last_name or ''}".strip()[:25] or user.username

    user_info = chat_info_collection.find_one({"user_id": user.id})
    if user_info and user_info.get("is_blocked"):
        logging.info(f"Ignoring message from blocked user {user.id}")
        return

    if chat_type in ["group", "supergroup"]:
        if user_input.lower() == "mitsuri":
            return
        
        now = time.time()
        if chat_id in GROUP_COOLDOWN and now - GROUP_COOLDOWN[chat_id] < 5:
            return
        GROUP_COOLDOWN[chat_id] = now
        
        is_mention = context.bot.username and context.bot.username.lower() in user_input.lower()
        is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
        mitsuri_pattern = re.compile(r'\b[Mm]itsuri\b')
        is_name_mentioned = mitsuri_pattern.search(user_input)

        if not (is_mention or is_reply or is_name_mentioned):
            return

        if is_mention:
            user_input = re.sub(r'\@' + re.escape(context.bot.username), '', user_input, flags=re.I).strip()
        
        if is_name_mentioned:
            user_input = mitsuri_pattern.sub('', user_input).strip()
        
        if not user_input:
            return
    
    save_chat_info(chat_id, user=user, chat=chat)

    history = context.chat_data.setdefault("history", [])
    history.append(("user", user_input))
    if len(history) > 6:
        history = history[-6:]
    prompt = build_prompt(history, user_input, chosen_name)

    try:
        context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception as e:
        logging.warning(f"Typing animation failed: {e}")

    reply = generate_with_retry(prompt)
    history.append(("bot", reply))
    context.chat_data["history"] = history
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

# === Main ===
if __name__ == "__main__":
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("show", show_chats))
    dp.add_handler(CommandHandler("eval", eval_command))
    
    dp.add_handler(MessageHandler(Filters.regex(r"^[Mm]itsuri$") & Filters.chat_type.group, mitsuri_hi))
    
    dp.add_handler(MessageHandler(
        (Filters.text & ~Filters.command & Filters.chat_type.group & (Filters.reply | Filters.entity("mention") | Filters.regex(r"\b[Mm]itsuri\b")))
        | (Filters.text & ~Filters.command & Filters.chat_type.private),
        handle_message
    ))

    dp.add_handler(ChatMemberHandler(track_bot_added_removed, ChatMemberHandler.MY_CHAT_MEMBER))
    dp.add_handler(CallbackQueryHandler(show_callback))
    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()
