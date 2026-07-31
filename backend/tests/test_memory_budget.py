"""Phase 6.7.5 Companion grounding budget tests."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.memory import MemoryType
from app.schemas.memory import (
    ApprovedMemorySearchResponse,
    RankedApprovedMemoryItem,
)
from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.memory.grounding import (
    CompanionMemoryGrounding,
    MemoryGroundingBudget,
)


def memory(memory_id: int, *, size: int = 12) -> RankedApprovedMemoryItem:
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    return RankedApprovedMemoryItem(
        memory_id=memory_id,
        memory_type=MemoryType.ATOMIC,
        category="story",
        title=f"Memory {memory_id}",
        summary="x" * size,
        importance=3,
        extraction_confidence=Decimal("0.8"),
        created_at=now,
        updated_at=now,
        relevance_score=max(0.01, 1 - memory_id / 100),
        matched_terms=["memory"],
    )


class FakeRetrieval:
    def __init__(self, memories):
        self.memories = memories

    def search_approved(self, db, *, user_id, legacy_id, query):
        return ApprovedMemorySearchResponse(
            legacy_id=legacy_id,
            matched_memory_count=len(self.memories),
            memories=self.memories,
        )


class FakeAI:
    async def generate_response(self, messages):
        return "response"

    async def stream_response(self, messages):
        yield "response"


def fake_db():
    db = MagicMock()
    limited = (
        db.query.return_value.filter.return_value.order_by.return_value
        .limit.return_value
    )
    limited.all.return_value = []
    return db


class MemoryBudgetTests(unittest.IsolatedAsyncioTestCase):
    def grounding(
        self,
        *,
        count=8,
        tokens=1500,
        characters=6000,
    ):
        return CompanionMemoryGrounding(
            MemoryGroundingBudget(
                max_memories=count,
                max_estimated_tokens=tokens,
                max_characters=characters,
            )
        )

    def test_maximum_memory_count_is_respected_in_ranking_order(self):
        selection = self.grounding(count=3).select(
            [memory(index) for index in range(1, 10)]
        )
        self.assertEqual(
            [item.memory_id for item in selection.memories],
            [1, 2, 3],
        )

    def test_duplicates_keep_the_first_highest_ranked_instance(self):
        highest = memory(1, size=5)
        duplicate = memory(1, size=100)
        selection = self.grounding().select(
            [highest, memory(2), duplicate]
        )
        self.assertEqual(
            [item.memory_id for item in selection.memories],
            [1, 2],
        )
        self.assertEqual(selection.memories[0].summary, highest.summary)

    def test_oversized_memory_is_skipped_and_later_memory_is_considered(self):
        baseline = self.grounding().select([memory(2, size=5)])
        character_limit = len(baseline.context)
        selection = self.grounding(
            tokens=10000,
            characters=character_limit,
        ).select([memory(1, size=1000), memory(2, size=5)])
        self.assertEqual(
            [item.memory_id for item in selection.memories],
            [2],
        )

    def test_exact_character_and_token_boundaries_are_inclusive(self):
        item = memory(1)
        unbounded = self.grounding().select([item])
        exact_characters = len(unbounded.context)
        exact_tokens = CompanionMemoryGrounding.estimate_tokens(
            unbounded.context
        )
        selection = self.grounding(
            tokens=exact_tokens,
            characters=exact_characters,
        ).select([item])
        self.assertEqual([entry.memory_id for entry in selection.memories], [1])

    def test_token_budget_is_respected(self):
        item = memory(1)
        context = self.grounding().select([item]).context
        required = CompanionMemoryGrounding.estimate_tokens(context)
        selection = self.grounding(
            tokens=required - 1,
            characters=10000,
        ).select([item])
        self.assertEqual(selection.memories, ())
        self.assertIsNone(selection.context)

    def test_empty_and_single_memory_behavior(self):
        grounding = self.grounding()
        self.assertEqual(grounding.select([]).memories, ())
        self.assertIsNone(grounding.select([]).context)
        selected = grounding.select([memory(1)])
        self.assertEqual(len(selected.memories), 1)
        self.assertIn("APPROVED LEGACY MEMORIES", selected.context)

    def test_many_memory_selection_is_deterministic(self):
        items = [memory(index) for index in range(1, 30)]
        grounding = self.grounding(count=5)
        first = grounding.select(items)
        second = grounding.select(items)
        self.assertEqual(first, second)

    def test_configuration_override_changes_selection(self):
        items = [memory(1), memory(2)]
        self.assertEqual(len(self.grounding(count=1).select(items).memories), 1)
        self.assertEqual(len(self.grounding(count=2).select(items).memories), 2)

    async def test_provenance_contains_only_grounded_memories(self):
        grounding = self.grounding(count=2)
        service = ChatService(
            FakeAI(),
            ContextBuilder(8),
            FakeRetrieval([memory(1), memory(2), memory(3)]),
            grounding,
        )
        conversation = SimpleNamespace(
            conversation_id=4,
            user_id=7,
            legacy_id=12,
        )
        generated = await service.generate_response_with_provenance(
            fake_db(), conversation, "memory"
        )
        streamed = service.stream_response_with_provenance(
            fake_db(), conversation, "memory"
        )
        self.assertEqual(generated.memory_ids, (1, 2))
        self.assertEqual(streamed.memory_ids, (1, 2))
        self.assertEqual(
            [part async for part in streamed.stream],
            ["response"],
        )

    def test_default_budget_preserves_normal_grounded_behavior(self):
        selection = CompanionMemoryGrounding().select([memory(1)])
        self.assertEqual([item.memory_id for item in selection.memories], [1])
        self.assertIn("Memory 1", selection.context)


if __name__ == "__main__":
    unittest.main()
