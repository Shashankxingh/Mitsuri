import asyncio
import logging

from mitsuri.ai.errors import RateLimitError, TransientProviderError, PermanentProviderError

logger = logging.getLogger(__name__)


class ProviderFallback:
    def __init__(self, providers, model_resolver, max_attempts=2, backoff_seconds=1):
        self.providers = providers
        self.model_resolver = model_resolver
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    async def generate(self, messages, use_large, temperature, max_tokens, top_p):
        last_error = None
        for provider in self.providers:
            model = self.model_resolver(provider.name, use_large)
            for attempt in range(self.max_attempts):
                try:
                    result = await provider.generate(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        top_p=top_p,
                    )
                    logger.info("✅ AI response from %s", result.provider)
                    return result
                except RateLimitError as exc:
                    last_error = exc
                    logger.warning(
                        "⚠️ %s rate limited; skipping retries",
                        provider.name,
                    )
                    break
                except TransientProviderError as exc:
                    last_error = exc
                    logger.warning(
                        "⚠️ %s transient error (attempt %s/%s)",
                        provider.name,
                        attempt + 1,
                        self.max_attempts,
                    )
                except PermanentProviderError as exc:
                    last_error = exc
                    logger.error("❌ %s permanent error: %s", provider.name, exc)
                    break

                await asyncio.sleep(self.backoff_seconds)

            logger.info("➡️ Falling back from %s to next provider", provider.name)

        if last_error:
            raise last_error
        raise RuntimeError("No providers configured")
