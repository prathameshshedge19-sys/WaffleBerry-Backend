import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import Settings
from app.services.rya import ChatTurn, OpenAIRyaProvider


def test_openai_provider_uses_configured_model_and_low_reasoning():
    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-only-secret-with-sufficient-length-123456",
        openai_api_key="test-key",
        ai_model="gpt-5.6-luna",
        ai_reasoning_effort="low",
    )
    provider = OpenAIRyaProvider(settings)
    provider.client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="I am Rya.")
    )

    answer = asyncio.run(provider.respond([ChatTurn(role="user", content="Who are you?")]))

    assert answer == "I am Rya."
    request = provider.client.responses.create.await_args.kwargs
    assert request["model"] == "gpt-5.6-luna"
    assert request["reasoning"] == {"effort": "low"}
    assert request["input"][0]["role"] == "system"
    assert "You are Rya" in request["input"][0]["content"]
