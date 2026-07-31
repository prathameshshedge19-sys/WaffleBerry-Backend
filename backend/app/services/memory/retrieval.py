"""Owner-scoped approved-memory retrieval foundation."""

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD, MemoryCRUD
from app.schemas.memory import (
    ApprovedMemorySearchResponse,
    ApprovedMemoryRetrievalItem,
    ApprovedMemoryRetrievalResponse,
)
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker


class MemoryRetrievalNotFoundError(Exception):
    """Raised for missing and non-owned Legacies alike."""


class MemoryRetrievalService:
    """Retrieve normalized approved memories without ranking or prompting."""

    def retrieve_approved(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
    ) -> ApprovedMemoryRetrievalResponse:
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryRetrievalNotFoundError(
                "Legacy was not found."
            )
        memories = MemoryCRUD.list_approved_for_retrieval(db, legacy_id)
        items = [
            ApprovedMemoryRetrievalItem.model_validate(memory)
            for memory in memories
        ]
        return ApprovedMemoryRetrievalResponse(
            legacy_id=legacy_id,
            approved_memory_count=len(items),
            memories=items,
        )

    def search_approved(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        query: str,
    ) -> ApprovedMemorySearchResponse:
        """Rank only approved memories after enforcing Legacy ownership."""
        retrieved = self.retrieve_approved(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
        )
        memories = MemoryRelevanceRanker().rank(retrieved.memories, query)
        return ApprovedMemorySearchResponse(
            legacy_id=legacy_id,
            matched_memory_count=len(memories),
            memories=memories,
        )
