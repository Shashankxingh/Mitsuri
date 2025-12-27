import logging

from mitsuri.ai.cerebras_provider import CerebrasProvider
from mitsuri.ai.fallback import ProviderFallback
from mitsuri.ai.groq_provider import GroqProvider
from mitsuri.ai.sambanova_provider import SambaNovaProvider
from mitsuri.config import PROVIDER_ORDER

logger = logging.getLogger(__name__)


def build_fallback(model_resolver):
    providers = []
    for provider_name in PROVIDER_ORDER:
        try:
            if provider_name == "groq":
                providers.append(GroqProvider())
            elif provider_name == "cerebras":
                providers.append(CerebrasProvider())
            elif provider_name == "sambanova":
                providers.append(SambaNovaProvider())
        except ValueError as exc:
            logger.warning("Skipping %s provider: %s", provider_name, exc)
    return ProviderFallback(providers=providers, model_resolver=model_resolver)
