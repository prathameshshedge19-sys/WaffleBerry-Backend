"""Phase 9.15 generation-contract tests for faithful memory grounding."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.models.memory import MemoryType
from app.schemas.memory import RankedApprovedMemoryItem
from app.services.ai.prompt_builder import PromptBuilder
from app.services.memory.grounding import CompanionMemoryGrounding


class FaithfulMemoryGroundingTests(unittest.TestCase):
    def memory(self, memory_id, summary, *, uncertainty_note=None):
        now = datetime(2026, 8, 6, tzinfo=timezone.utc)
        return RankedApprovedMemoryItem(
            memory_id=memory_id,
            memory_type=MemoryType.ATOMIC,
            category="story",
            title="Approved memory",
            summary=summary,
            importance=3,
            extraction_confidence=Decimal("0.9"),
            created_at=now,
            updated_at=now,
            relevance_score=0.9,
            uncertainty_note=uncertainty_note,
        )

    def test_persona_prioritizes_direct_faithful_memory_answers(self):
        prompt = PromptBuilder.build_legacy_persona_system_prompt(
            display_name="Mom",
            relationship="Mother",
        )
        normalized = " ".join(prompt.split())
        for instruction in (
            "those memories must dominate the response",
            "Stay as close as possible to their stated meaning",
            "must not improve, embellish, soften, explain, interpret, or expand",
            "state the supported part clearly and stop",
            "Never append uncertainty after giving a supported answer",
        ):
            self.assertIn(instruction, normalized)

    def test_faithful_translation_example_rejects_invented_intensity(self):
        prompt = PromptBuilder.build_legacy_persona_system_prompt(
            display_name="Mom",
            relationship="Mother",
        )
        self.assertIn("My favourite subject was History.", prompt)
        self.assertIn("remembering and intensity were not stated", prompt)

    def test_absent_uncertainty_does_not_permit_invented_forgetting(self):
        context = CompanionMemoryGrounding().build_context([
            self.memory(1, "Pushkar maza lahan bhau aahe."),
            self.memory(2, "Mi tyala maraichi."),
        ])
        self.assertIn('"uncertainty_note": null', context)
        self.assertIn("do not add uncertainty, forgetting language", context)
        self.assertIn("state those facts and stop", context)
        self.assertIn("Pushkar maza lahan bhau aahe.", context)
        self.assertIn("Mi tyala maraichi.", context)

    def test_recorded_uncertainty_is_preserved_without_new_qualifiers(self):
        context = CompanionMemoryGrounding().build_context([
            self.memory(
                1,
                "We may have moved in 1998.",
                uncertainty_note="The year was approximate.",
            )
        ])
        self.assertIn("We may have moved in 1998.", context)
        self.assertIn("The year was approximate.", context)
        self.assertIn(
            "do not invent details or erase recorded uncertainty",
            context.lower(),
        )

    def test_work_memory_is_passed_verbatim_without_extra_biography(self):
        source = "Mi Mumbai madhye teacher mhanun kaam kel."
        context = CompanionMemoryGrounding().build_context([
            self.memory(1, source)
        ])
        self.assertIn(source, context)
        self.assertIn("do not improve, embellish, interpret", context)


if __name__ == "__main__":
    unittest.main()
