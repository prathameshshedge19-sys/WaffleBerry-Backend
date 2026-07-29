"""Provider-neutral AI contracts."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Sequence


AIMessageRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class AIMessage:
    """A provider-neutral chat message."""

    role: AIMessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("AI message content must not be blank.")


class AIProvider(ABC):
    """Interface implemented by every AI provider adapter."""

    @abstractmethod
    async def generate_response(
        self,
        messages: Sequence[AIMessage],
    ) -> str:
        """Generate assistant text from provider-neutral messages."""
        raise NotImplementedError

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
    ) -> AsyncIterator[str]:
        """Stream assistant text when supported by the provider."""
        if False:
            yield ""
        raise NotImplementedError
