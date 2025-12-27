import os

from dotenv import load_dotenv

load_dotenv()

# API Keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = os.getenv("OWNER_ID")

# NO REDIS NEEDED! Uses in-memory cache instead (100% FREE)

ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1002759296936"))

# Model Configuration
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

# Rate Limiting (In-Memory - FREE!)
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "10"))
SMALL_TALK_MAX_TOKENS = int(os.getenv("SMALL_TALK_MAX_TOKENS", "4"))

# Performance Tuning (100% FREE)
MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "50"))
MONGO_MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", "10"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "6"))
MAX_HISTORY_STORED = int(os.getenv("MAX_HISTORY_STORED", "20"))

# Caching (In-Memory - FREE!)
CACHE_COMMON_RESPONSES = os.getenv("CACHE_COMMON_RESPONSES", "true").lower() == "true"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

# Broadcasting (100% FREE)
BROADCAST_BATCH_SIZE = int(os.getenv("BROADCAST_BATCH_SIZE", "30"))
BROADCAST_BATCH_DELAY = float(os.getenv("BROADCAST_BATCH_DELAY", "1.0"))

# Group cooldown (In-Memory - FREE!)
GROUP_COOLDOWN_SECONDS = int(os.getenv("GROUP_COOLDOWN_SECONDS", "3"))

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