"""Post-retrieval support analysis for grounded Persona responses."""

import enum
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.memory import Memory, MemoryReviewStatus
from app.schemas.memory import RankedApprovedMemoryItem


class RetrievalSupportLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class MemoryFidelityPlan:
    """Internal evidence assessment; labels are never rendered to the model."""

    support_level: RetrievalSupportLevel
    combine_related: bool
    has_conflict: bool
    has_uncertainty: bool

    def prompt_guidance(self) -> str:
        if self.has_conflict:
            return (
                "The approved evidence for this topic has a recorded conflict. "
                "Do not choose an account or merge incompatible details. Speak "
                "in the first person with natural uncertainty, for example that "
                "you remember it differently or are not certain."
            )
        if self.support_level == RetrievalSupportLevel.LOW:
            return (
                "The available factual support is insufficient or explicitly "
                "uncertain. Do not guess, infer a relationship, date, location, "
                "event, or missing detail. Respond briefly in the first person "
                "with natural uncertainty."
            )
        if self.combine_related:
            return (
                "Several compatible approved memories support this topic. "
                "Combine only their stated facts into one natural first-person "
                "answer. Preserve each uncertainty and never add a causal link, "
                "sequence, date, location, relationship, or transition that the "
                "memories do not state."
            )
        return (
            "Approved evidence supports a focused answer. Use only its stated "
            "facts, preserve any qualifications, and do not expand beyond it."
        )


class MemoryFidelityAnalyzer:
    """Classify selected evidence deterministically without changing ranking."""

    def analyze(
        self,
        memories: list[RankedApprovedMemoryItem],
        *,
        retrieval_available: bool = True,
        has_conflict: bool = False,
        has_uncertainty: bool = False,
    ) -> MemoryFidelityPlan:
        if (
            not retrieval_available
            or not memories
            or has_conflict
            or has_uncertainty
        ):
            level = RetrievalSupportLevel.LOW
        else:
            relevance = [Decimal(str(item.relevance_score)) for item in memories]
            confidences = [
                self._decimal(item.extraction_confidence)
                for item in memories
            ]
            high_confidence = all(
                value is not None and value >= Decimal("0.700")
                for value in confidences
            )
            average_relevance = sum(relevance) / len(relevance)
            if (
                len(memories) >= 2
                and high_confidence
                and average_relevance >= Decimal("0.500")
            ):
                level = RetrievalSupportLevel.HIGH
            else:
                level = RetrievalSupportLevel.MEDIUM
        return MemoryFidelityPlan(
            support_level=level,
            combine_related=(len(memories) > 1 and not has_conflict),
            has_conflict=has_conflict,
            has_uncertainty=has_uncertainty,
        )

    @staticmethod
    def _decimal(value) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return None
        return result if result.is_finite() else None


class MemoryFidelityService:
    """Inspect metadata only for memories already selected for grounding."""

    def __init__(self, analyzer: MemoryFidelityAnalyzer | None = None):
        self._analyzer = analyzer or MemoryFidelityAnalyzer()

    def analyze_selected(
        self,
        db: Session,
        *,
        legacy_id: int,
        memories: list[RankedApprovedMemoryItem],
        retrieval_available: bool = True,
    ) -> MemoryFidelityPlan:
        if not memories:
            return self._analyzer.analyze(
                [],
                retrieval_available=retrieval_available,
            )
        selected_ids = [item.memory_id for item in memories]
        metadata = (
            db.query(
                Memory.memory_id,
                Memory.contradiction_group_id,
                Memory.uncertainty_note,
            )
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.review_status == MemoryReviewStatus.APPROVED,
                Memory.memory_id.in_(selected_ids),
            )
            .all()
        )
        return self._analyzer.analyze(
            memories,
            retrieval_available=retrieval_available,
            has_conflict=any(
                item.contradiction_group_id is not None for item in metadata
            ),
            has_uncertainty=any(
                isinstance(item.uncertainty_note, str)
                and bool(item.uncertainty_note.strip())
                for item in metadata
            ),
        )
