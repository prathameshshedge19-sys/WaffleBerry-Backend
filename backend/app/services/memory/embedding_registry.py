"""Central provider registry for multilingual memory embeddings."""

from collections.abc import Callable

from app.config import Settings
from app.services.ai.exceptions import AIConfigurationError
from app.services.memory.embedding import EmbeddingProvider
from app.services.memory.openai_embedding import OpenAIEmbeddingProvider


EmbeddingProviderFactory = Callable[[Settings], EmbeddingProvider]
EMBEDDING_PROVIDER_REGISTRY: dict[str, EmbeddingProviderFactory] = {
    "openai": OpenAIEmbeddingProvider,
}


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    name = settings.memory_embedding_provider.strip().casefold()
    factory = EMBEDDING_PROVIDER_REGISTRY.get(name)
    if factory is None:
        raise AIConfigurationError(
            f"Unsupported memory embedding provider: {name or '<empty>'}."
        )
    return factory(settings)
