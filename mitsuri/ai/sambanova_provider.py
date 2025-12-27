import asyncio
import logging

from sambanova import SambaNova

from mitsuri.ai.base import Provider, ProviderResult
from mitsuri.ai.errors import RateLimitError, TransientProviderError, PermanentProviderError
from mitsuri.config import SAMBANOVA_API_KEY

logger = logging.getLogger(__name__)


class SambaNovaProvider(Provider):
    name = "sambanova"

    def __init__(self):
        if not SAMBANOVA_API_KEY:
            raise ValueError("❌ Missing SAMBANOVA_API_KEY")
        self.client = SambaNova(
            api_key=SAMBANOVA_API_KEY,
            base_url="https://api.sambanova.ai/v1",
        )

    async def generate(self, messages, model, temperature, max_tokens, top_p):
        try:
            completion = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=model,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            content = completion.choices[0].message.content.strip()
            return ProviderResult(content=content, provider=self.name)
        except Exception as exc:
            logger.warning("SambaNova error: %s", exc)
            status = getattr(exc, "status_code", None)
            if status == 429 or "rate limit" in str(exc).lower():
                raise RateLimitError(str(exc)) from exc
            if status and status >= 500:
                raise TransientProviderError(str(exc)) from exc
            raise PermanentProviderError(str(exc)) from exc
