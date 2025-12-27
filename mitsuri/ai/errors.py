class ProviderError(Exception):
    """Base error for provider failures."""


class RateLimitError(ProviderError):
    """Provider is rate limiting."""


class TransientProviderError(ProviderError):
    """Temporary errors (timeouts, 5xx)."""


class PermanentProviderError(ProviderError):
    """Permanent errors (invalid auth, invalid request)."""
