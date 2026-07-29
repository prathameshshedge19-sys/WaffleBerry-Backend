"""Unit tests for deterministic, non-persistent memory validation."""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from app.models.memory import MemoryReviewStatus, MemoryType
from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryDetails,
    MemoryParticipantCreate,
    MemoryProvenanceCreate,
    TemporalReference,
)
from app.services.memory.provenance import (
    ProvenanceSourceRecord,
    RegisteredProvenanceVerifier,
)
from app.services.memory.validation import MemoryValidationService
from app.services.memory.validation_contracts import (
    MemoryValidationAction,
    MemoryValidationStatus,
)


def candidate(
    *,
    summary="Mom was born in Pune in 1968.",
    category="personal_detail",
    details=None,
    uncertainty_note=None,
    provenance=None,
    importance=5,
    extraction_confidence=Decimal("0.960"),
):
    return MemoryCandidateCreate(
        memory_type=MemoryType.ATOMIC,
        category=category,
        title="A preserved memory",
        summary=summary,
        details=details or MemoryDetails(),
        emotional_significance=None,
        importance=importance,
        extraction_confidence=extraction_confidence,
        uncertainty_note=uncertainty_note,
        participants=[
            MemoryParticipantCreate(
                name="Mom",
                relationship="mother",
                role="subject",
            )
        ],
        tags=["family"],
        provenance=provenance
        or [
            MemoryProvenanceCreate(
                source_type="story_session",
                story_session_id=10,
                story_message_id=100,
                speaker="user",
                excerpt="I was born in Pune in 1968",
                chapter="Childhood",
            )
        ],
    )


def existing_memory(
    memory_id,
    *,
    summary,
    category="personal_detail",
    legacy_id=42,
    details=None,
    uncertainty_note=None,
    review_status=MemoryReviewStatus.APPROVED,
    tags=(),
):
    return SimpleNamespace(
        memory_id=memory_id,
        legacy_id=legacy_id,
        memory_type=MemoryType.ATOMIC,
        category=category,
        title="Existing memory",
        summary=summary,
        details=details or {},
        uncertainty_note=uncertainty_note,
        review_status=review_status,
        participants=[
            SimpleNamespace(
                name="Mom",
                relationship="mother",
                role="subject",
            )
        ],
        tag_links=[
            SimpleNamespace(tag=SimpleNamespace(name=tag))
            for tag in tags
        ],
    )


def story_verifier(
    *,
    legacy_id=42,
    speaker="user",
    content="I was born in Pune in 1968.",
):
    return RegisteredProvenanceVerifier(
        [
            ProvenanceSourceRecord(
                source_type="story_session",
                legacy_id=legacy_id,
                story_session_id=10,
                story_message_id=100,
                speaker=speaker,
                content=content,
            )
        ]
    )


class MemoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.service = MemoryValidationService()

    def validate(
        self,
        memory_candidate,
        *,
        existing=(),
        verifier=None,
    ):
        return self.service.validate_candidate(
            memory_candidate,
            legacy_id=42,
            existing_memories=list(existing),
            provenance_verifier=verifier or story_verifier(),
        )

    def test_normalization_preserves_meaning_and_extraction_confidence(self):
        raw = candidate().model_dump(mode="python")
        raw["category"] = " Personal-Detail "
        raw["title"] = "  a   preserved memory!!! "
        raw["summary"] = "  mom was born in Pune in 1968!!  "
        raw["tags"] = [" Family ", "family", "EARLY LIFE"]

        result = self.validate(raw)

        self.assertEqual(result.status, MemoryValidationStatus.ACCEPTED)
        normalized = result.normalized_candidate
        self.assertEqual(normalized.category, "personal_detail")
        self.assertEqual(normalized.title, "A preserved memory!")
        self.assertEqual(
            normalized.summary,
            "Mom was born in Pune in 1968!",
        )
        self.assertEqual(normalized.tags, ["family", "early life"])
        self.assertEqual(
            normalized.extraction_confidence,
            Decimal("0.960"),
        )
        self.assertEqual(
            result.validation_confidence,
            Decimal("0.800"),
        )

    def test_exact_duplicate_is_not_recommended_for_persistence(self):
        extracted = candidate()
        existing = existing_memory(
            1,
            summary="  mom was born in Pune in 1968. ",
        )

        result = self.validate(extracted, existing=[existing])

        self.assertEqual(result.status, MemoryValidationStatus.DUPLICATE)
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.DO_NOT_PERSIST,
        )
        self.assertEqual(result.related_memory_ids, [1])

    def test_related_claim_is_possible_enrichment_without_merge(self):
        extracted = candidate(
            summary="Mom taught mathematics.",
            category="achievement",
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="user",
                    excerpt="I taught mathematics",
                )
            ],
        )
        existing = existing_memory(
            2,
            summary="Mom worked as a teacher.",
            category="achievement",
        )
        verifier = story_verifier(content="I taught mathematics.")

        result = self.validate(
            extracted,
            existing=[existing],
            verifier=verifier,
        )

        self.assertEqual(
            result.status,
            MemoryValidationStatus.POSSIBLE_ENRICHMENT,
        )
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.REVIEW_ENRICHMENT,
        )
        self.assertEqual(result.related_memory_ids, [2])

    def test_reworded_claim_is_possible_duplicate(self):
        extracted = candidate(
            summary="Mom valued honesty & independence.",
            category="value",
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="user",
                    excerpt="I valued honesty & independence",
                )
            ],
        )
        existing = existing_memory(
            3,
            summary="Mom valued honesty and independence.",
            category="value",
            tags=["family"],
        )

        result = self.validate(
            extracted,
            existing=[existing],
            verifier=story_verifier(
                content="I valued honesty & independence."
            ),
        )

        self.assertEqual(
            result.status,
            MemoryValidationStatus.POSSIBLE_DUPLICATE,
        )
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.REVIEW_LINK,
        )

    def test_conflicting_certain_years_are_a_contradiction(self):
        extracted = candidate(
            summary="Mom was born in 1967.",
            details=MemoryDetails(
                temporal_references=[
                    TemporalReference(
                        text="1967",
                        start_date="1967-01-01",
                        end_date="1967-12-31",
                        precision="year",
                        certainty="stated",
                    )
                ]
            ),
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="user",
                    excerpt="I was born in 1967",
                )
            ],
        )
        existing = existing_memory(
            4,
            summary="Mom was born in 1968.",
            details={
                "temporal_references": [
                    {
                        "text": "1968",
                        "start_date": "1968-01-01",
                        "end_date": "1968-12-31",
                        "precision": "year",
                        "is_approximate": False,
                        "certainty": "stated",
                    }
                ],
                "places": [],
            },
        )

        result = self.validate(
            extracted,
            existing=[existing],
            verifier=story_verifier(content="I was born in 1967."),
        )

        self.assertEqual(
            result.status,
            MemoryValidationStatus.CONTRADICTION,
        )
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.REVIEW_CONTRADICTION,
        )
        self.assertEqual(result.related_memory_ids, [4])

    def test_uncertain_year_is_not_declared_a_contradiction(self):
        extracted = candidate(
            summary="Mom may have been born around 1967.",
            uncertainty_note="The speaker was unsure of the exact year.",
            details=MemoryDetails(
                temporal_references=[
                    TemporalReference(
                        text="around 1967",
                        precision="year",
                        is_approximate=True,
                        certainty="uncertain",
                    )
                ]
            ),
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="user",
                    excerpt="I think I was born around 1967",
                )
            ],
        )
        existing = existing_memory(
            5,
            summary="Mom was born in 1968.",
        )

        result = self.validate(
            extracted,
            existing=[existing],
            verifier=story_verifier(
                content="I think I was born around 1967."
            ),
        )

        self.assertNotEqual(
            result.status,
            MemoryValidationStatus.CONTRADICTION,
        )

    def test_missing_provenance_source_is_invalid(self):
        result = self.validate(
            candidate(),
            verifier=RegisteredProvenanceVerifier([]),
        )

        self.assertEqual(result.status, MemoryValidationStatus.INVALID)
        self.assertEqual(result.issues[0].code, "missing_source")

    def test_assistant_source_is_invalid(self):
        assistant_candidate = candidate(
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="assistant",
                    excerpt="I was born in Pune in 1968",
                )
            ]
        )

        result = self.validate(
            assistant_candidate,
            verifier=story_verifier(speaker="assistant"),
        )

        self.assertEqual(result.status, MemoryValidationStatus.INVALID)
        self.assertEqual(result.issues[0].code, "assistant_source")

    def test_cross_legacy_source_is_invalid(self):
        result = self.validate(
            candidate(),
            verifier=story_verifier(legacy_id=99),
        )

        self.assertEqual(result.status, MemoryValidationStatus.INVALID)
        self.assertEqual(result.issues[0].code, "cross_legacy_source")

    def test_fabricated_excerpt_is_invalid(self):
        result = self.validate(
            candidate(),
            verifier=story_verifier(
                content="This source says something else."
            ),
        )

        self.assertEqual(result.status, MemoryValidationStatus.INVALID)
        self.assertEqual(result.issues[0].code, "fabricated_excerpt")

    def test_out_of_range_fields_are_invalid_not_clamped(self):
        raw = candidate().model_dump(mode="python")
        raw["importance"] = 9
        raw["extraction_confidence"] = Decimal("1.500")

        result = self.validate(raw)

        self.assertEqual(result.status, MemoryValidationStatus.INVALID)
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.REJECT_CANDIDATE,
        )
        self.assertIsNone(result.normalized_candidate)

    def test_vague_candidate_is_insufficient_information(self):
        vague = candidate(
            summary="It was nice.",
            category="story",
            details=MemoryDetails(),
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=10,
                    story_message_id=100,
                    speaker="user",
                    excerpt="It was nice",
                )
            ],
        )
        vague.participants = []

        result = self.validate(
            vague,
            verifier=story_verifier(content="It was nice."),
        )

        self.assertEqual(
            result.status,
            MemoryValidationStatus.INSUFFICIENT_INFORMATION,
        )
        self.assertEqual(
            result.recommended_action,
            MemoryValidationAction.REQUEST_MORE_INFORMATION,
        )

    def test_other_legacy_memories_are_never_compared(self):
        other_legacy = existing_memory(
            6,
            legacy_id=99,
            summary="Mom was born in Pune in 1968.",
        )

        result = self.validate(candidate(), existing=[other_legacy])

        self.assertEqual(result.status, MemoryValidationStatus.ACCEPTED)
        self.assertEqual(result.related_memory_ids, [])

    def test_future_document_source_uses_same_verifier_interface(self):
        document_candidate = candidate(
            provenance=[
                MemoryProvenanceCreate(
                    source_type="document",
                    source_locator={"document_id": 501, "page": 1},
                    speaker="source_document",
                    excerpt="Date of birth: August 1968",
                )
            ]
        )
        verifier = RegisteredProvenanceVerifier(
            [
                ProvenanceSourceRecord(
                    source_type="document",
                    legacy_id=42,
                    source_locator={"page": 1, "document_id": 501},
                    speaker="source_document",
                    content="Date of birth: August 1968",
                )
            ]
        )

        result = self.validate(document_candidate, verifier=verifier)

        self.assertEqual(result.status, MemoryValidationStatus.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
