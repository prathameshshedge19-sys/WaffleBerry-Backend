"""Provider-neutral AI exceptions with safe machine-readable codes."""


class AIServiceError(Exception):
    """Base exception for errors raised by the AI service layer."""

    code = "ai_service_error"

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AIConfigurationError(AIServiceError):
    """Raised when AI configuration is missing or invalid."""

    code = "configuration_error"


class AIProviderError(AIServiceError):
    """Raised when an AI provider cannot complete a request."""

    code = "provider_error"


class AIRateLimitError(AIProviderError):
    """Raised for temporary provider request throttling."""

    code = "rate_limited"


class AIQuotaExceededError(AIProviderError):
    """Raised when provider quota or billing capacity is exhausted."""

    code = "quota_exceeded"


class AIAuthenticationError(AIProviderError):
    """Raised when provider credentials are rejected."""

    code = "authentication_error"


class AITimeoutError(AIProviderError):
    """Raised when the provider request times out."""

    code = "timeout"


class AIConnectionError(AIProviderError):
    """Raised when the provider cannot be reached."""

    code = "connection_error"


class AIProviderUnavailableError(AIProviderError):
    """Raised when a provider is temporarily unavailable."""

    code = "provider_unavailable"


class AIInvalidResponseError(AIServiceError):
    """Raised when an AI provider returns an unusable response."""

    code = "invalid_response"


# Backwards-compatible name retained for existing integrations.
AIResponseError = AIInvalidResponseError
