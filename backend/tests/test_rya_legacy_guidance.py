"""Prompt-delivery contracts, not a substitute for live-model behavior evals."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.legacy_persona import OpenAILegacyPersonaProvider
from app.services.rya import ChatTurn, OpenAIRyaProvider, RYA_SYSTEM_PROMPT


class FakeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __aiter__(self):
        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="Synthetic answer")
            yield SimpleNamespace(type="response.completed")
        return events()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["rya", "legacy"])
def test_guidance_reaches_only_builder_text_requests(streaming, mode):
    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-only-secret-with-sufficient-length-123456",
        openai_api_key="test-key",
        ai_model="gpt-5.6-luna",
        ai_reasoning_effort="low",
    )
    provider = (OpenAIRyaProvider if mode == "rya" else OpenAILegacyPersonaProvider)(settings)
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(output_text="Synthetic answer")

    def stream(**kwargs):
        requests.append(kwargs)
        return FakeStream()

    provider.client.responses.create = AsyncMock(side_effect=create)
    provider.client.responses.stream = stream
    turns = [ChatTurn("system", "Synthetic authorized identity context"),
             ChatTurn("user", "Dad, do you remember our trip?")]

    async def run():
        if streaming:
            return "".join([part async for part in provider.stream(turns)])
        return await provider.respond(turns)

    assert asyncio.run(run()) == "Synthetic answer"
    assert len(requests) == 1
    request = requests[0]
    original = [{"role": turn.role, "content": turn.content} for turn in turns]
    assert request["input"] == (
        [{"role": "system", "content": RYA_SYSTEM_PROMPT}] + original if mode == "rya" else original
    )
    assert request["model"] == settings.ai_model
    assert request["reasoning"] == {"effort": "low"}


def test_guidance_contract_includes_route_and_false_positive_guards():
    for instruction in (
        "BUILDER / LEGACY CHAT CONFUSION:",
        "open Access in this chat's sidebar, copy the Legacy code",
        "choose Talk with a Legacy, paste the code, then select Begin conversation",
        "not the COL- collaborator code",
        "obtain the Legacy code from the owner instead",
        "never invent a code",
        "do not append a new interview question",
        "not the actual person",
        "Do not redirect ordinary builder questions",
        "quoted dialogue",
        "Match their current language and script",
        "Once they return to building, continue normally without repeating it",
    ):
        assert instruction in RYA_SYSTEM_PROMPT
