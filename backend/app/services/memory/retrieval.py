"""Owner-scoped approved-memory retrieval foundation."""

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD, MemoryCRUD
from app.schemas.memory import (
    ApprovedMemorySearchResponse,
    ApprovedMemoryRetrievalItem,
    ApprovedMemoryRetrievalResponse,
)
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker
from app.models.memory import LegacyStatus


class MemoryRetrievalNotFoundError(Exception):
    """Raised for missing and non-owned Legacies alike."""


class MemoryRetrievalArchivedError(Exception):
    """Raised when active Companion grounding targets an archived Legacy."""


class MemoryRetrievalService:
    """Retrieve normalized approved memories without ranking or prompting."""

    def retrieve_approved(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        allow_archived: bool = False,
    ) -> ApprovedMemoryRetrievalResponse:
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise MemoryRetrievalNotFoundError(
                "Legacy was not found."
            )
        if legacy.status == LegacyStatus.ARCHIVED and not allow_archived:
            raise MemoryRetrievalArchivedError(
                "Restore this Legacy before continuing."
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
        allow_archived: bool = False,
    ) -> ApprovedMemorySearchResponse:
        """Rank only approved memories after enforcing Legacy ownership."""
        retrieved = self.retrieve_approved(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
            allow_archived=allow_archived,
        )
        memories = MemoryRelevanceRanker().rank(retrieved.memories, query)
        response = ApprovedMemorySearchResponse(
            legacy_id=legacy_id,
            matched_memory_count=len(memories),
            memories=memories,
        )
        response._approved_memory_count = retrieved.approved_memory_count
        return response
