"""Application-level AI exceptions."""


class AIServiceError(Exception):
    """Base exception for errors raised by the AI service layer."""


class AIConfigurationError(AIServiceError):
    """Raised when AI configuration is missing or invalid."""


class AIProviderError(AIServiceError):
    """Raised when an AI provider cannot complete a request."""


class AIRateLimitError(AIProviderError):
    """Raised when the provider rejects a request for quota or rate limits."""


class AIAuthenticationError(AIProviderError):
    """Raised when provider credentials are rejected."""


class AITimeoutError(AIProviderError):
    """Raised when the provider request times out."""


class AIConnectionError(AIProviderError):
    """Raised when the provider cannot be reached."""


class AIResponseError(AIServiceError):
    """Raised when an AI provider returns an unusable response."""
