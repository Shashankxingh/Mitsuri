import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass

from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from mitsuri.ai.manager import build_fallback
from mitsuri.cache import cache
from mitsuri.config import (
    MODEL_LARGE,
    MODEL_SMALL,
    CEREBRAS_MODEL_LARGE,
    CEREBRAS_MODEL_SMALL,
    SAMBANOVA_MODEL_LARGE,
    SAMBANOVA_MODEL_SMALL,
    SMALL_TALK_MAX_TOKENS,
    GROUP_COOLDOWN_SECONDS,
    BROADCAST_BATCH_SIZE,
    BROADCAST_BATCH_DELAY,
    CACHE_COMMON_RESPONSES,
)
from mitsuri.storage import (
    save_user,
    get_chat_history,
    save_chat_history,
    get_all_chat_ids,
    get_stats,
)
from mitsuri.utils import format_text_to_html

logger = logging.getLogger(__name__)


SMALL_TALK_PATTERNS = re.compile(
    r"^(hi|hello|hey|hii|yo|sup|how are you|how r u|good morning|good night|"
    r"good evening|hola|namaste|hey there|hi there|hlo|wassup|whats up|"
    r"how's it going)\b",
    re.IGNORECASE,
)


def is_small_talk(text):
    """Detect if message is small talk to use faster/cheaper model."""
    tokens = re.findall(r"\w+|[^\w\s]", text)
    token_count = len(tokens)
    if token_count <= SMALL_TALK_MAX_TOKENS:
        return True
    return bool(SMALL_TALK_PATTERNS.match(text.strip()))


def resolve_model(provider_name, use_large):
    """Map provider names to their model strings."""
    if provider_name == "cerebras":
        return CEREBRAS_MODEL_LARGE if use_large else CEREBRAS_MODEL_SMALL
    if provider_name == "sambanova":
        return SAMBANOVA_MODEL_LARGE if use_large else SAMBANOVA_MODEL_SMALL
    return MODEL_LARGE if use_large else MODEL_SMALL


@dataclass
class BotState:
    chat_collection: object
    history_collection: object
    owner_id: int
    admin_group_id: int
    provider_fallback: object


def build_state(chat_collection, history_collection, owner_id, admin_group_id):
    """Build bot state with optimized components."""
    return BotState(
        chat_collection=chat_collection,
        history_collection=history_collection,
        owner_id=owner_id,
        admin_group_id=admin_group_id,
        provider_fallback=build_fallback(resolve_model),
    )


async def get_ai_response(state, history, user_input, user_name):
    """
    Get AI response with intelligent caching.
    Checks cache first, then calls AI if needed.
    """
    system_prompt = (
        "You are Mitsuri Kanroji from Demon Slayer. "
        "Personality: Romantic, bubbly, cheerful, and sweet. Use emojis sparingly (🍡, 💖). "
        "Language: Hinglish (mix of Hindi and English). "
        "Keep responses concise and natural - around 1-3 sentences. Be warm and friendly!"
    )

    # Check cache for common responses
    if CACHE_COMMON_RESPONSES and is_small_talk(user_input):
        cached = await cache.get_common_response(user_input)
        if cached:
            return cached

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
        
        response = result.content
        
        # Cache common responses for future use
        if CACHE_COMMON_RESPONSES and is_small_talk(user_input):
            await cache.cache_common_response(user_input, response)
        
        return response
        
    except Exception as exc:
        logger.error("❌ Provider fallback failed: %s", exc)
        return "Ah! Something went wrong... 😵‍💫 Please try again!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    logger.info("🚀 /start triggered by %s (ID: %s)", user.first_name, user.id)
    
    # Save user asynchronously
    state = context.bot_data["state"]
    await asyncio.to_thread(save_user, state.chat_collection, update)

    welcome_msg = (
        "Kyaa~! 💖 Hii! I am <b>Mitsuri Kanroji</b>!\n\n"
        "I love making new friends! Let's chat and eat mochi together! 🍡\n\n"
        "Use /help to see what I can do~"
    )
    await update.message.reply_html(welcome_msg)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command with latency measurement."""
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
    """Handle /help command."""
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
    """Handle admin button press."""
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
    """
    Handle incoming messages with optimized flow:
    1. Rate limiting via Redis
    2. Cache checking
    3. AI generation if needed
    4. Async database operations
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = context.bot_data["state"]

    # Rate limiting check (Redis-based)
    if not await cache.check_rate_limit(user.id):
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

    # Group cooldown check (Redis-based)
    if not is_private:
        if not await cache.check_group_cooldown(chat_id, GROUP_COOLDOWN_SECONDS):
            logger.info("⏳ Cooldown active for group %s", chat_id)
            return

    model_type = "Small" if is_small_talk(text) else "Large"
    logger.info(
        "📩 [%s] Message from %s (ID: %s): %s...",
        model_type,
        user.first_name,
        user.id,
        text[:30],
    )

    await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    
    # Save user in background
    asyncio.create_task(
        asyncio.to_thread(save_user, state.chat_collection, update)
    )

    # Get history and generate response
    history = await asyncio.to_thread(get_chat_history, state.history_collection, chat_id)
    response = await get_ai_response(state, history, text, user.first_name)

    # Save history in background (non-blocking)
    asyncio.create_task(
        asyncio.to_thread(save_chat_history, state.history_collection, chat_id, "user", text)
    )
    asyncio.create_task(
        asyncio.to_thread(save_chat_history, state.history_collection, chat_id, "assistant", response)
    )

    # Send response
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
    """Decorator to restrict commands to admin group only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        state = context.bot_data["state"]
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if user_id != state.owner_id:
            logger.warning("⚠️ Unauthorized admin command attempt by %s", user_id)
            return
        if chat_id != state.admin_group_id:
            await update.message.reply_text("⚠️ Admin commands only work in admin group!")
            return

        return await func(update, context, *args, **kwargs)

    return wrapper


@admin_group_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get bot statistics with optimized queries."""
    logger.info("📊 Admin requested stats")
    state = context.bot_data["state"]
    
    try:
        # Run stats query in thread to avoid blocking
        stats_data = await asyncio.to_thread(
            get_stats,
            state.chat_collection,
            state.history_collection
        )
        
        await update.message.reply_html(
            f"<b>📊 Mitsuri's Stats</b>\n\n"
            f"👤 <b>Users:</b> {stats_data['users']:,}\n"
            f"👥 <b>Groups:</b> {stats_data['groups']:,}\n"
            f"💬 <b>Total Messages:</b> {stats_data['messages']:,}"
        )
    except Exception as exc:
        logger.error("❌ Error fetching stats: %s", exc)
        await update.message.reply_text("Failed to fetch stats!")


@admin_group_only
async def cast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Broadcast message with optimized parallel sending.
    Handles 100k+ users efficiently with batching.
    """
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /cast [Message]")
        return

    logger.info("📢 Starting broadcast: %s...", msg[:30])
    status_msg = await update.message.reply_text("🚀 Preparing broadcast...")
    state = context.bot_data["state"]
    
    broadcast_id = str(uuid.uuid4())
    formatted_msg = format_text_to_html(msg)
    
    success = 0
    failed = 0
    total = 0

    try:
        # Process in batches for efficiency
        batch_num = 0
        
        for batch in get_all_chat_ids(state.chat_collection, BROADCAST_BATCH_SIZE):
            batch_num += 1
            total += len(batch)
            
            # Send batch in parallel
            tasks = [
                context.bot.send_message(
                    chat_id=chat_id,
                    text=formatted_msg,
                    parse_mode="HTML",
                )
                for chat_id in batch
            ]
            
            # Execute batch concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count results
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                else:
                    success += 1
            
            # Update status every 5 batches
            if batch_num % 5 == 0:
                await status_msg.edit_text(
                    f"📤 Broadcasting...\n"
                    f"✅ Sent: {success:,}\n"
                    f"❌ Failed: {failed:,}\n"
                    f"📊 Progress: {success + failed:,} / ~{total:,}"
                )
            
            # Rate limit delay between batches
            await asyncio.sleep(BROADCAST_BATCH_DELAY)
        
        logger.info("📢 Broadcast finished. Success: %d, Failed: %d", success, failed)
        
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"📤 Sent: {success:,}\n"
            f"❌ Failed: {failed:,}\n"
            f"📊 Total: {total:,}",
            parse_mode="HTML",
        )
        
    except Exception as exc:
        logger.error("❌ Broadcast error: %s", exc)
        await status_msg.edit_text("❌ Broadcast failed!")