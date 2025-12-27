import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = os.getenv("OWNER_ID")

ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1002759296936"))

MODEL_LARGE = os.getenv("MODEL_LARGE", "llama-3.3-70b-versatile")
MODEL_SMALL = os.getenv("MODEL_SMALL", "llama-3.1-8b-instant")

CEREBRAS_MODEL_LARGE = os.getenv("CEREBRAS_MODEL_LARGE", "llama-3.3-70b")
CEREBRAS_MODEL_SMALL = os.getenv("CEREBRAS_MODEL_SMALL", "llama3.1-8b")

SAMBANOVA_MODEL_LARGE = os.getenv("SAMBANOVA_MODEL_LARGE", "Meta-Llama-3.3-70B-Instruct")
SAMBANOVA_MODEL_SMALL = os.getenv("SAMBANOVA_MODEL_SMALL", "Meta-Llama-3.1-8B-Instruct")

PROVIDER_ORDER = [
    provider.strip().lower()
    for provider in os.getenv("PROVIDER_ORDER", "groq,cerebras,sambanova").split(",")
    if provider.strip()
]

RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "10"))
SMALL_TALK_MAX_TOKENS = int(os.getenv("SMALL_TALK_MAX_TOKENS", "4"))

def require_env():
    missing = [
        name
        for name, value in [
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("MONGO_URI", MONGO_URI),
            ("OWNER_ID", OWNER_ID),
        ]
        if not value
    ]
    if missing:
        raise ValueError(
            "❌ Missing required environment variables: "
            + ", ".join(missing)
        )

    try:
        owner_id = int(OWNER_ID)
    except ValueError as exc:
        raise ValueError("❌ OWNER_ID must be a valid integer!") from exc

    return owner_id
