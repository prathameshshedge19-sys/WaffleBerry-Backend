"""OpenAI adapter for the provider-neutral embedding contract."""

from collections.abc import Sequence

import httpx
from openai import OpenAI, OpenAIError

from app.config import Settings
from app.services.memory.embedding import EmbeddingProvider, EmbeddingProviderError


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings):
        self._model = settings.memory_embedding_model.strip()
        self._dimensions = settings.memory_embedding_dimensions
        self._version = settings.memory_embedding_version.strip()
        timeout = httpx.Timeout(
            connect=settings.ai_connect_timeout_seconds,
            read=settings.ai_read_timeout_seconds,
            write=settings.ai_connect_timeout_seconds,
            pool=settings.ai_connect_timeout_seconds,
        )
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=timeout,
            max_retries=0,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=list(texts),
                dimensions=self.dimensions,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            return [list(item.embedding) for item in ordered]
        except (OpenAIError, AttributeError, TypeError, ValueError):
            raise EmbeddingProviderError("Embedding provider was unavailable.") from None
