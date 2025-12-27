import asyncio
import logging

from cerebras.cloud.sdk import Cerebras

from mitsuri.ai.base import Provider, ProviderResult
from mitsuri.ai.errors import RateLimitError, TransientProviderError, PermanentProviderError
from mitsuri.config import CEREBRAS_API_KEY

logger = logging.getLogger(__name__)


class CerebrasProvider(Provider):
    name = "cerebras"

    def __init__(self):
        if not CEREBRAS_API_KEY:
            raise ValueError("❌ Missing CEREBRAS_API_KEY")
        self.client = Cerebras(api_key=CEREBRAS_API_KEY)

    async def generate(self, messages, model, temperature, max_tokens, top_p):
        try:
            completion = await asyncio.to_thread(
                self.client.chat.completions.create,
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=False,
            )
            content = completion.choices[0].message.content.strip()
            return ProviderResult(content=content, provider=self.name)
        except Exception as exc:
            logger.warning("Cerebras error: %s", exc)
            status = getattr(exc, "status_code", None)
            if status == 429 or "rate limit" in str(exc).lower():
                raise RateLimitError(str(exc)) from exc
            if status and status >= 500:
                raise TransientProviderError(str(exc)) from exc
            raise PermanentProviderError(str(exc)) from exc
