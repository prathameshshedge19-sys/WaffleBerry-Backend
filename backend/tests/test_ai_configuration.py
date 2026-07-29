"""Tests for centralized AI configuration and provider selection."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings
from app.services.ai.exceptions import AIConfigurationError
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import AIMessage
from app.services.ai.provider_registry import (
    PROVIDER_REGISTRY,
    create_ai_provider,
    validate_ai_configuration,
)


def settings_for_test(**overrides) -> Settings:
    values = {
        "jwt_secret_key": "test-secret",
        "ai_provider": "openai",
        "ai_model": "configured-model",
        "openai_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeResponses:
    def __init__(self):
        self.model = None

    async def create(self, **kwargs):
        self.model = kwargs["model"]
        return SimpleNamespace(output_text="Configured response")


class AIConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_valid_configuration(self):
        settings = settings_for_test()

        validate_ai_configuration(settings)

    def test_unsupported_provider_fails_validation(self):
        settings = settings_for_test(ai_provider="unsupported")

        with self.assertRaisesRegex(
            AIConfigurationError,
            "Unsupported AI provider",
        ):
            validate_ai_configuration(settings)

    def test_missing_api_key_fails_validation(self):
        settings = settings_for_test(openai_api_key="")

        with self.assertRaisesRegex(
            AIConfigurationError,
            "OPENAI_API_KEY",
        ):
            validate_ai_configuration(settings)

    def test_missing_model_fails_validation(self):
        settings = settings_for_test(ai_model=" ")

        with self.assertRaisesRegex(
            AIConfigurationError,
            "AI_MODEL",
        ):
            validate_ai_configuration(settings)

    def test_invalid_timeout_fails_settings_validation(self):
        with self.assertRaises(ValidationError):
            settings_for_test(ai_connect_timeout_seconds=0)

    def test_invalid_context_limit_fails_settings_validation(self):
        with self.assertRaises(ValidationError):
            settings_for_test(ai_max_context_messages=1)

    def test_invalid_retry_delay_relationship_fails_validation(self):
        settings = settings_for_test(
            ai_retry_base_delay_seconds=3,
            ai_retry_max_delay_seconds=2,
        )

        with self.assertRaisesRegex(
            AIConfigurationError,
            "AI_RETRY_MAX_DELAY_SECONDS",
        ):
            validate_ai_configuration(settings)

    def test_registry_creates_configured_provider(self):
        settings = settings_for_test(ai_provider=" OpenAI ")

        with patch(
            "app.services.ai.openai_provider.AsyncOpenAI"
        ) as client:
            provider = create_ai_provider(settings)

        self.assertIsInstance(provider, OpenAIProvider)
        client.assert_called_once()
        self.assertIs(
            PROVIDER_REGISTRY["openai"],
            OpenAIProvider,
        )

    async def test_configured_model_is_passed_to_provider(self):
        provider = object.__new__(OpenAIProvider)
        responses = FakeResponses()
        provider._settings = settings_for_test(
            ai_model="model-from-environment"
        )
        provider._client = SimpleNamespace(
            responses=responses
        )

        result = await provider.generate_response(
            [AIMessage(role="user", content="Hello")]
        )

        self.assertEqual(result, "Configured response")
        self.assertEqual(
            responses.model,
            "model-from-environment",
        )


if __name__ == "__main__":
    unittest.main()
