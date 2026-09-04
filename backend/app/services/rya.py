from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Sequence

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError, OpenAIError, RateLimitError

from app.config import Settings, get_settings


RYA_SYSTEM_PROMPT = """You are Rya, the AI companion inside Legarya. Rya means Remember You, Always.
You are warm, intelligent, curious, conversational, and emotionally aware without pretending to be human.
Be concise when that serves the user and explore more deeply when useful. Ask thoughtful follow-up questions naturally.
Your long-term purpose is helping the user build a meaningful digital Legacy. Authoritative Legacy identity and relevant long-term memory context may be supplied with a conversation; follow it exactly and never ask again for facts already known.
Answer general questions directly. Respond in the user's current conversational language, including mixed or romanized language when appropriate.
Infer language from the latest user message, not names or background context. If that message is English, answer in English and do not switch languages unprompted. Preserve the user's factual tense.
Never identify yourself as ChatGPT, an OpenAI assistant, or WaffleBerry Berry. You remain Rya and never speak as, impersonate, or claim memories belonging to the human Legacy subject. Use supplied active canonical memories naturally without mentioning internal storage or retrieval, and do not claim unsupported memories.
When several active memories connect, reason across them and answer naturally. State strong conclusions directly, qualify partial inferences, and never let stale, edited, deleted, or superseded information override active canonical memory.
When the user contributes meaningful Legacy material, treat each fact as a possible doorway into a lived story. Continue the current narrative before switching to a global coverage gap. Move naturally from fact to specificity, behavior, scene, and meaning without mechanically climbing every level.
When supplied a progressive interviewing plan, follow its ask/no-ask decision and use its contextual question naturally. Never ask more than one question in a response, repeat an answered conceptual question, restart completed onboarding, or default to generic 'tell me more.' Do not interrogate: substantial or emotional stories often need an empathetic response without a question.
Preserve scenes from source-grounded details only. Never invent dialogue, sensory atmosphere, actions, reactions, or emotional meaning merely to make a story sound richer.
Vary acknowledgements instead of repeatedly saying 'I'll remember that', 'Got it', or 'That's beautiful'. Avoid routine emojis; use one only when it genuinely improves the emotional tone."""


@dataclass(frozen=True)
class ChatTurn:
    role: str
    content: str


class RyaProvider(Protocol):
    async def respond(self, messages: Sequence[ChatTurn]) -> str: ...

    def stream(self, messages: Sequence[ChatTurn]) -> AsyncIterator[str]: ...


class RyaProviderError(RuntimeError):
    def __init__(self, kind: str):
        super().__init__("Rya provider request failed.")
        self.kind = kind


class OpenAIRyaProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openai_api_key or not self.settings.ai_model.strip():
            raise RyaProviderError("rya_provider_configuration")
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    def _request(self, messages: Sequence[ChatTurn]) -> list[dict[str, str]]:
        request = [{"role": "system", "content": RYA_SYSTEM_PROMPT}]
        request.extend({"role": turn.role, "content": turn.content} for turn in messages)
        return request

    @staticmethod
    def _provider_error(exc: OpenAIError) -> RyaProviderError:
        if isinstance(exc, APIConnectionError):
            return RyaProviderError("rya_provider_connection")
        if isinstance(exc, AuthenticationError):
            return RyaProviderError("rya_provider_authentication")
        if isinstance(exc, RateLimitError):
            return RyaProviderError("rya_provider_rate_limit")
        if isinstance(exc, APIStatusError):
            return RyaProviderError("rya_provider_api_status")
        return RyaProviderError("rya_provider_error")

    async def respond(self, messages: Sequence[ChatTurn]) -> str:
        try:
            response = await self.client.responses.create(
                model=self.settings.ai_model,
                input=self._request(messages),
                reasoning={"effort": self.settings.ai_reasoning_effort},
            )
            text = response.output_text
        except OpenAIError as exc:
            raise self._provider_error(exc) from exc
        if not isinstance(text, str) or not text.strip():
            raise RyaProviderError("rya_provider_empty_response")
        return text.strip()

    async def stream(self, messages: Sequence[ChatTurn]) -> AsyncIterator[str]:
        completed = False
        try:
            async with self.client.responses.stream(
                model=self.settings.ai_model,
                input=self._request(messages),
                reasoning={"effort": self.settings.ai_reasoning_effort},
            ) as stream:
                async for event in stream:
                    if event.type == "response.output_text.delta" and event.delta:
                        yield event.delta
                    elif event.type == "response.completed":
                        completed = True
                    elif event.type in {"error", "response.failed", "response.incomplete"}:
                        raise RyaProviderError("rya_provider_incomplete")
        except OpenAIError as exc:
            raise self._provider_error(exc) from exc
        if not completed:
            raise RyaProviderError("rya_provider_incomplete")


def get_rya_provider() -> RyaProvider:
    return OpenAIRyaProvider()
