"""Provider-neutral, versioned multilingual memory embedding services."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.memory import Memory
from app.schemas.memory import ApprovedMemoryRetrievalItem
from app.services.memory.multilingual_retrieval import normalize_embedding_text


class EmbeddingProviderError(Exception):
    """Safe boundary for unavailable or invalid embedding providers."""


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def memory_embedding_text(memory: ApprovedMemoryRetrievalItem) -> str:
    values = [memory.title, memory.summary, memory.category]
    return normalize_embedding_text("\n".join(value for value in values if value))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


@dataclass(frozen=True)
class SemanticScores:
    scores: dict[int, float]
    route_used: bool


class MemoryEmbeddingService:
    """Create compatible embeddings and score only pre-scoped memories."""

    def __init__(self, provider: EmbeddingProvider, *, threshold: float = 0.35):
        self.provider = provider
        self.threshold = threshold

    def score(
        self,
        db: Session,
        memories: list[ApprovedMemoryRetrievalItem],
        query: str,
    ) -> SemanticScores:
        if not memories:
            return SemanticScores({}, True)
        try:
            self._ensure_compatible_embeddings(db, memories)
            query_vector = self.provider.embed([normalize_embedding_text(query)])[0]
        except (EmbeddingProviderError, IndexError, TypeError, ValueError):
            db.rollback()
            return SemanticScores({}, False)
        scores = {}
        for memory in memories:
            vector = memory.embedding
            if self._compatible(memory) and vector is not None:
                score = cosine_similarity(query_vector, vector)
                if score >= self.threshold:
                    scores[memory.memory_id] = score
        return SemanticScores(scores, True)

    def _ensure_compatible_embeddings(
        self, db: Session, memories: list[ApprovedMemoryRetrievalItem]
    ) -> None:
        stale = [memory for memory in memories if not self._compatible(memory)]
        if not stale:
            return
        vectors = self.provider.embed([memory_embedding_text(item) for item in stale])
        if len(vectors) != len(stale):
            raise EmbeddingProviderError("Embedding count did not match input count.")
        now = datetime.now(timezone.utc)
        for item, vector in zip(stale, vectors):
            if len(vector) != self.provider.dimensions:
                raise EmbeddingProviderError("Embedding dimensions were incompatible.")
            row = db.query(Memory).filter(Memory.memory_id == item.memory_id).one()
            row.embedding = vector
            row.embedding_model = self.provider.model
            row.embedding_version = self.provider.version
            row.embedding_dimensions = self.provider.dimensions
            row.embedded_at = now
            item.embedding = vector
            item.embedding_model = self.provider.model
            item.embedding_version = self.provider.version
            item.embedding_dimensions = self.provider.dimensions
            item.embedded_at = now
        db.commit()

    def _compatible(self, memory: ApprovedMemoryRetrievalItem) -> bool:
        return (
            memory.embedding is not None
            and memory.embedding_model == self.provider.model
            and memory.embedding_version == self.provider.version
            and memory.embedding_dimensions == self.provider.dimensions
            and len(memory.embedding) == self.provider.dimensions
        )
