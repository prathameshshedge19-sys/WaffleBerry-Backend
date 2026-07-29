"""Central registry and validation for configured AI providers."""

from collections.abc import Callable

from app.config import Settings
from app.services.ai.exceptions import AIConfigurationError
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import AIProvider


ProviderFactory = Callable[[Settings], AIProvider]

PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    "openai": OpenAIProvider,
}


def normalize_provider_name(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def validate_ai_configuration(settings: Settings) -> None:
    """Fail early for unsupported or incomplete AI configuration."""
    provider_name = normalize_provider_name(settings.ai_provider)
    provider_factory = PROVIDER_REGISTRY.get(provider_name)
    if provider_factory is None:
        raise AIConfigurationError(
            f"Unsupported AI provider: {provider_name or '<empty>'}."
        )

    if not isinstance(settings.ai_model, str) or not settings.ai_model.strip():
        raise AIConfigurationError("AI_MODEL must be configured.")

    if (
        settings.ai_retry_max_delay_seconds
        < settings.ai_retry_base_delay_seconds
    ):
        raise AIConfigurationError(
            "AI_RETRY_MAX_DELAY_SECONDS must be greater than or equal to "
            "AI_RETRY_BASE_DELAY_SECONDS."
        )

    validator = getattr(provider_factory, "validate_configuration", None)
    if validator is not None:
        validator(settings)


def create_ai_provider(settings: Settings) -> AIProvider:
    """Create the configured provider behind the neutral interface."""
    validate_ai_configuration(settings)
    provider_name = normalize_provider_name(settings.ai_provider)
    return PROVIDER_REGISTRY[provider_name](settings)
