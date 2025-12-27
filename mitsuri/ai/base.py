from dataclasses import dataclass


@dataclass
class ProviderResult:
    content: str
    provider: str


class Provider:
    name: str

    async def generate(self, messages, model, temperature, max_tokens, top_p):
        raise NotImplementedError
