"""Feedback F1 contract tests for Guided Story episode consolidation."""

import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.models.memory import (
    Legacy,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
)
from app.schemas.memory import ApprovedMemoryRetrievalItem
from app.services.ai.prompt_builder import PromptBuilder
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker


class FakeAIService:
    def __init__(self, memories):
        self.response = json.dumps({"memories": memories}, ensure_ascii=False)

    async def generate_response(
        self,
        _messages,
        *,
        structured_response_schema=None,
    ):
        self.schema = structured_response_schema
        return self.response


def extracted(title, summary, evidence):
    return {
        "memory_type": "narrative",
        "category": "life_event",
        "title": title,
        "summary": summary,
        "details": {},
        "emotional_significance": None,
        "importance": 4,
        "extraction_confidence": 0.95,
        "uncertainty_note": None,
        "participants": [],
        "tags": ["travel"],
        "evidence": [
            {"source_message_id": message_id, "excerpt": excerpt}
            for message_id, excerpt in evidence
        ],
    }


class StoryMemoryConsolidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.legacy = Legacy(
            legacy_id=42,
            owner_user_id=7,
            display_name="Mom",
            relationship="Mother",
        )
        self.story = StorySession(
            story_session_id=310,
            legacy_id=42,
            chapter_key="travel",
            created_by_user_id=7,
        )
        answers = (
            "I travelled to Manali when I was around 20.",
            "I played a snow fight with my husband. He cooked Maggi for me.",
            "I learnt sunlight matters for mental health; Delhi sunlight gave me hope.",
        )
        self.messages = [
            StoryMessage(
                story_message_id=880 + index,
                story_session_id=310,
                role=StoryMessageRole.USER,
                content=answer,
                sequence=index + 1,
            )
            for index, answer in enumerate(answers)
        ]

    def test_prompt_requires_episode_merge_without_overwrite_or_cross_event_merge(self):
        prompt = PromptBuilder.build_memory_extraction_system_prompt()
        normalized_prompt = " ".join(prompt.split())
        for rule in (
            "same specific life event",
            "retain every distinct supported fact once",
            "never replace an earlier fact with a later one",
            "different trips, schools, jobs, relationships, or time periods",
            "return separate memories",
        ):
            self.assertIn(rule, normalized_prompt)

    async def test_follow_up_answers_form_one_source_grounded_story(self):
        summary = (
            "Mom travelled to Manali at around 20, enjoyed a snow fight with her "
            "husband, who cooked Maggi, learnt that sunlight matters for mental "
            "health, and felt hope in Delhi sunlight."
        )
        output = extracted(
            "Manali trip",
            summary,
            [(message.story_message_id, message.content) for message in self.messages],
        )
        candidates = await MemoryExtractionService(
            FakeAIService([output])
        ).extract_story_session(self.legacy, self.story, self.messages)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].memory_type, MemoryType.NARRATIVE)
        self.assertEqual(candidates[0].summary, summary)
        self.assertEqual(
            {item.story_message_id for item in candidates[0].provenance},
            {880, 881, 882},
        )

    async def test_different_trips_remain_separate_stories(self):
        messages = [
            StoryMessage(
                story_message_id=890,
                story_session_id=310,
                role=StoryMessageRole.USER,
                content="I visited snowy Manali.",
                sequence=1,
            ),
            StoryMessage(
                story_message_id=891,
                story_session_id=310,
                role=StoryMessageRole.USER,
                content="Years later I visited Goa.",
                sequence=2,
            ),
        ]
        outputs = [
            extracted(
                "Manali trip",
                "Mom visited snowy Manali.",
                [(890, messages[0].content)],
            ),
            extracted(
                "Goa trip",
                "Mom later visited Goa.",
                [(891, messages[1].content)],
            ),
        ]
        candidates = await MemoryExtractionService(
            FakeAIService(outputs)
        ).extract_story_session(self.legacy, self.story, messages)
        self.assertEqual(
            [item.title for item in candidates],
            ["Manali trip", "Goa trip"],
        )

    def test_retrieval_and_grounding_receive_the_complete_story(self):
        now = datetime.now(timezone.utc)
        summary = (
            "At 20 I visited Manali, saw snowfall, had a snow fight, ate "
            "Maggi, and learnt sunlight matters."
        )
        item = ApprovedMemoryRetrievalItem(
            memory_id=1,
            memory_type=MemoryType.NARRATIVE,
            category="life_event",
            title="Manali trip",
            summary=summary,
            importance=4,
            extraction_confidence=Decimal("0.95"),
            created_at=now,
            updated_at=now,
        )
        ranked = MemoryRelevanceRanker().rank([item], "Tell me about Manali")
        self.assertEqual([memory.memory_id for memory in ranked], [1])
        context = CompanionMemoryGrounding().build_context(ranked)
        self.assertIn(summary, context)
        self.assertEqual(context.count(summary), 1)


if __name__ == "__main__":
    unittest.main()
