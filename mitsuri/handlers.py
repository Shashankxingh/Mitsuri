import asyncio
import datetime
import html
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass

from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from mitsuri.ai.manager import build_fallback
from mitsuri.config import (
    MODEL_LARGE,
    MODEL_SMALL,
    CEREBRAS_MODEL_LARGE,
    CEREBRAS_MODEL_SMALL,
    SAMBANOVA_MODEL_LARGE,
    SAMBANOVA_MODEL_SMALL,
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_MAX,
    SMALL_TALK_MAX_TOKENS,
)
from mitsuri.storage import save_user, get_chat_history, save_chat_history

logger = logging.getLogger(__name__)


def format_text_to_html(text):
    if not text:
        return ""
    text = html.escape(text)
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.*?)`", r"<code>\1</code>", text)
    return text


def check_rate_limit(user_id, user_rate_limit):
    now = time.time()
    timestamps = user_rate_limit[user_id]

    timestamps[:] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]

    if len(timestamps) >= RATE_LIMIT_MAX:
        return False

    timestamps.append(now)
    return True


@dataclass
class BotState:
    chat_collection: object
    history_collection: object
    owner_id: int
    admin_group_id: int
    provider_fallback: object
    group_cooldown: dict
    user_rate_limit: dict


SMALL_TALK_PATTERNS = re.compile(
    r"^(hi|hello|hey|hii|yo|sup|how are you|how r u|good morning|good night|"
    r"good evening|hola|namaste|hey there|hi there|hlo|wassup|whats up|"
    r"how's it going)\b",
    re.IGNORECASE,
)


def is_small_talk(text):
    tokens = re.findall(r"\w+|[^\w\s]", text)
    token_count = len(tokens)
    if token_count <= SMALL_TALK_MAX_TOKENS:
        return True
    return bool(SMALL_TALK_PATTERNS.match(text.strip()))


def resolve_model(provider_name, use_large):
    if provider_name == "cerebras":
        return CEREBRAS_MODEL_LARGE if use_large else CEREBRAS_MODEL_SMALL
    if provider_name == "sambanova":
        return SAMBANOVA_MODEL_LARGE if use_large else SAMBANOVA_MODEL_SMALL
    return MODEL_LARGE if use_large else MODEL_SMALL


def build_state(chat_collection, history_collection, owner_id, admin_group_id):
    return BotState(
        chat_collection=chat_collection,
        history_collection=history_collection,
        owner_id=owner_id,
        admin_group_id=admin_group_id,
        provider_fallback=build_fallback(resolve_model),
        group_cooldown={},
        user_rate_limit=defaultdict(list),
    )


async def get_ai_response(state, history, user_input, user_name):
    system_prompt = (
        "You are Mitsuri Kanroji from Demon Slayer. "
        "Personality: Romantic, bubbly, cheerful, and sweet. Use emojis sparingly (🍡, 💖). "
        "Language: Hinglish (mix of Hindi and English). "
        "Keep responses concise and natural - around 1-3 sentences. Be warm and friendly!"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f"{user_input} (User: {user_name})"})

    use_large = not is_small_talk(user_input)
    try:
        result = await state.provider_fallback.generate(
            messages=messages,
            use_large=use_large,
            temperature=0.8,
            max_tokens=150,
            top_p=0.9,
        )
        return result.content
    except Exception as exc:
        logger.error("❌ Provider fallback failed: %s", exc)
        return "Ah! Something went wrong... 😵‍💫 Please try again!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("🚀 /start triggered by %s (ID: %s)", user.first_name, user.id)
    save_user(context.bot_data["state"].chat_collection, update)

    welcome_msg = (
        "Kyaa~! 💖 Hii! I am <b>Mitsuri Kanroji</b>!\n\n"
        "I love making new friends! Let's chat and eat mochi together! 🍡\n\n"
        "Use /help to see what I can do~"
    )
    await update.message.reply_html(welcome_msg)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("🏓 /ping triggered by %s (ID: %s)", user.first_name, user.id)

    start_time = time.time()
    msg = await update.message.reply_text("🍡 Pinging...")
    end_time = time.time()
    bot_latency = (end_time - start_time) * 1000
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"⚡ <b>Latency:</b> <code>{bot_latency:.2f}ms</code>",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("ℹ️ /help requested by %s (ID: %s)", user.first_name, user.id)

    help_text = (
        "🌸 <b>Mitsuri's Help Menu</b> 🌸\n\n"
        "I am the Love Hashira! Here is what I can do:\n\n"
        "💬 <b>Chat:</b> Reply to me or mention me in groups!\n"
        "💌 <b>Private:</b> DM me to talk privately (smarter AI!).\n"
        "🗣️ <b>Language:</b> I speak Hinglish!\n"
        "⚡ <b>Utility:</b> Use /ping to check speed.\n\n"
        "<i>Just say 'Hi' to start chatting!</i> 💖"
    )

    state = context.bot_data["state"]
    if update.effective_user.id == state.owner_id:
        keyboard = [[InlineKeyboardButton("🔐 Admin Commands", callback_data="admin_help")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_html(help_text, reply_markup=reply_markup)
    else:
        await update.message.reply_html(help_text)


async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    state = context.bot_data["state"]
    if query.from_user.id != state.owner_id:
        logger.warning("⚠️ Unauthorized admin button press by %s", query.from_user.id)
        return

    admin_text = (
        "<b>👑 Admin Commands</b>\n"
        "<i>(Only work in Admin Group)</i>\n\n"
        "• <code>/stats</code> - Check user counts\n"
        "• <code>/cast [msg]</code> - Broadcast message\n"
    )
    await query.message.reply_html(admin_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = context.bot_data["state"]

    if not check_rate_limit(user.id, state.user_rate_limit):
        logger.warning("⚠️ Rate limit exceeded for user %s", user.id)
        return

    should_reply = False
    is_private = update.effective_chat.type == constants.ChatType.PRIVATE
    bot_username = context.bot.username

    if is_private:
        should_reply = True
    else:
        if (
            f"@{bot_username}" in text
            or (
                update.message.reply_to_message
                and update.message.reply_to_message.from_user.id == context.bot.id
            )
        ):
            should_reply = True
            text = text.replace(f"@{bot_username}", "").strip()
        elif "mitsuri" in text.lower():
            should_reply = True

    if not should_reply:
        return

    if not is_private:
        now = time.time()
        if chat_id in state.group_cooldown and now - state.group_cooldown[chat_id] < 3:
            logger.info("⏳ Cooldown active for group %s", chat_id)
            return
        state.group_cooldown[chat_id] = now

    model_type = "Adaptive"
    logger.info(
        "📩 [%s] Message from %s (ID: %s): %s...",
        model_type,
        user.first_name,
        user.id,
        text[:30],
    )

    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    save_user(state.chat_collection, update)

    history = get_chat_history(state.history_collection, chat_id)
    response = await get_ai_response(state, history, text, user.first_name)

    save_chat_history(state.history_collection, chat_id, "user", text)
    save_chat_history(state.history_collection, chat_id, "assistant", response)

    try:
        await update.message.reply_html(format_text_to_html(response))
        logger.info("📤 [%s] Sent reply to %s", model_type, chat_id)
    except Exception as exc:
        logger.error("❌ Failed to send reply to %s: %s", chat_id, exc)
        try:
            await update.message.reply_text(response)
        except Exception:
            logger.error("❌ Complete failure to send message to %s", chat_id)


def admin_group_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        state = context.bot_data["state"]
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if user_id != state.owner_id:
            logger.warning("⚠️ Unauthorized admin command attempt by %s", user_id)
            return
        if chat_id != state.admin_group_id:
            return

        return await func(update, context, *args, **kwargs)

    return wrapper


@admin_group_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📊 Admin requested stats")
    state = context.bot_data["state"]
    try:
        u_count = state.chat_collection.count_documents({"type": "private"})
        g_count = state.chat_collection.count_documents({"type": {"$ne": "private"}})
        total_msgs = state.history_collection.count_documents({})

        await update.message.reply_html(
            f"<b>📊 Mitsuri's Stats</b>\n\n"
            f"👤 <b>Users:</b> {u_count}\n"
            f"👥 <b>Groups:</b> {g_count}\n"
            f"💬 <b>Total Messages:</b> {total_msgs}"
        )
    except Exception as exc:
        logger.error("❌ Error fetching stats: %s", exc)
        await update.message.reply_text("Failed to fetch stats!")


@admin_group_only
async def cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /cast [Message]")
        return

    logger.info("📢 Starting broadcast: %s...", msg[:30])
    status_msg = await update.message.reply_text("🚀 Sending broadcast...")
    state = context.bot_data["state"]

    try:
        cursor = state.chat_collection.find({}, {"chat_id": 1})
        success, failed = 0, 0
        formatted_msg = format_text_to_html(msg)

        for doc in cursor:
            try:
                await context.bot.send_message(
                    chat_id=doc["chat_id"],
                    text=formatted_msg,
                    parse_mode="HTML",
                )
                success += 1
                await asyncio.sleep(0.05)
            except Exception as exc:
                logger.debug("Failed to send to %s: %s", doc["chat_id"], exc)
                failed += 1

        logger.info("📢 Broadcast finished. Success: %s, Failed: %s", success, failed)
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"📤 Sent: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("❌ Broadcast error: %s", exc)
        await status_msg.edit_text("❌ Broadcast failed!")
