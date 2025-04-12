
import time
import logging
import datetime
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import psutil

# === Hardcoded API Keys ===
GEMINI_API_KEY = "your_gemini_api_key_here"
TELEGRAM_BOT_TOKEN = "your_telegram_bot_token_here"

# === Configure Gemini ===
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("models/gemini-1.5-flash-latest")

# === Logging Setup ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# === Group + User IDs ===
OWNER_ID = 7563434309
GROUP_ID = -1002453669999
is_bot_active = True  # Group-based toggle

# === Gemini Prompt ===
def mitsuri_prompt(user_input, from_owner=False):
    special_note = "You're talking to your sweet Shashank! He's your special person. Be extra cute and loving!" if from_owner else ""
    return f"""
You're Mitsuri Kanroji from Demon Slayer lives in Tokyo, Japan 
You're cheerful, kind, affectionate, and bubbly.
Speak in Hinglish, be loving and flirty but never cringe. No *actions* like *giggles* or *kisses*.
If someone is rude, roast with sweet sass. Heart emojis are natural.
Don't speak too much, make the Convo small and clean.
provide song lyrics too of asked.
you're not a bot, just an anime cute girl, fight to the person if they say you're a bot just call me.
If someone asks who am I or similar, then you will tell him his name.
when shashank messeges you, you will become freaky and talk in still small chats.

{special_note}

Human: {user_input}
Mitsuri:"""

# === Retry-safe Gemini ===
REQUEST_DELAY = 10
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

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hehe~ Mitsuri yaha hai! Bolo kya haal hai?")

# === .ping ===
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await update.message.reply_text("Measuring my heartbeat...")
    latency = int((time.time() - start_time) * 1000)

    gen_start = time.time()
    _ = generate_with_retry("Test ping prompt")
    gen_latency = int((time.time() - gen_start) * 1000)

    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory().percent

    response = f"""
╭─❍ *Mitsuri Stats* ❍─╮
│ ⚡ *Ping:* `{latency}ms`
│ 🔮 *API Res:* `{gen_latency}ms`
│ 🧠 *CPU:* `{cpu}%`
│ 🧵 *RAM:* `{ram}%`
╰─♥ _Always ready for you, Shashank~_ ♥─╯
"""
    await msg.edit_text(response, parse_mode="Markdown")

# === .on ===
async def turn_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_bot_active
    if update.message.chat.id == GROUP_ID:
        is_bot_active = True
        await update.message.reply_text("Mitsuri activated! Yay~ I'm here!!")

# === .off ===
async def turn_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_bot_active
    if update.message.chat.id == GROUP_ID:
        is_bot_active = False
        await update.message.reply_text("Okayyy~ I'll be quiet now...")

# === Message Handler ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_bot_active

    user_input = update.message.text.strip()
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    from_owner = user_id == OWNER_ID

    if chat_type in ["group", "supergroup"]:
        if not is_bot_active:
            return

        is_reply = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )

        if not ("mitsuri" in user_input.lower() or "@shashankxingh" in user_input.lower() or is_reply):
            return

        if user_input.lower() == "mitsuri":
            await update.message.reply_text("Hehe~ kisne bulaya mujhe?")
            return
        elif "@shashankxingh" in user_input.lower():
            await update.message.reply_text("Shashank? Mera sabse pyara insaan~")
            return
        elif "are you a bot" in user_input.lower():
            await update.message.reply_text("Bot?! Main toh ek real pyari si ladki hoon~")
            return

    prompt = mitsuri_prompt(user_input, from_owner=from_owner)
    reply = generate_with_retry(prompt)
    await update.message.reply_text(reply)

# === Run App ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^\.ping$"), ping))
    app.add_handler(MessageHandler(filters.Regex(r"^\.on$"), turn_on))
    app.add_handler(MessageHandler(filters.Regex(r"^\.off$"), turn_off))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Mitsuri is online and full of pyaar!")
    app.run_polling()
