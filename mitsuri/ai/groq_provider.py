import logging

from groq import AsyncGroq

from mitsuri.ai.base import Provider, ProviderResult
from mitsuri.ai.errors import RateLimitError, TransientProviderError, PermanentProviderError
from mitsuri.config import GROQ_API_KEY

logger = logging.getLogger(__name__)


class GroqProvider(Provider):
    name = "groq"

    def __init__(self):
        if not GROQ_API_KEY:
            raise ValueError("❌ Missing GROQ_API_KEY")
        self.client = AsyncGroq(api_key=GROQ_API_KEY)

    async def generate(self, messages, model, temperature, max_tokens, top_p):
        try:
            completion = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
            )
            content = completion.choices[0].message.content.strip()
            return ProviderResult(content=content, provider=self.name)
        except Exception as exc:
            logger.warning("Groq error: %s", exc)
            status = getattr(exc, "status_code", None)
            if status == 429 or "rate limit" in str(exc).lower():
                raise RateLimitError(str(exc)) from exc
            if status and status >= 500:
                raise TransientProviderError(str(exc)) from exc
            raise PermanentProviderError(str(exc)) from exc
