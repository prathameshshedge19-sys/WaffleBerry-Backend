"""Deterministic validation between memory extraction and persistence."""

import copy
import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.models.memory import MemoryReviewStatus
from app.schemas.memory import MemoryCandidateCreate
from app.services.memory.provenance import ProvenanceVerifier
from app.services.memory.validation_contracts import (
    MemoryValidationAction,
    MemoryValidationIssue,
    MemoryValidationResult,
    MemoryValidationStatus,
)


class ExistingMemory(Protocol):
    """Persisted fields required for deterministic comparison."""

    memory_id: int
    legacy_id: int
    category: str
    summary: str
    details: object
    uncertainty_note: str | None
    review_status: object
    participants: object
    tag_links: object


_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_YEAR_PATTERN = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|21\d{2})\b")
_SPACE_PATTERN = re.compile(r"\s+")
_REPEATED_MARK_PATTERN = re.compile(r"([!?;,])\1+")
_LONG_ELLIPSIS_PATTERN = re.compile(r"\.{4,}")
_CATEGORY_SEPARATOR_PATTERN = re.compile(r"[\s-]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "the",
        "to",
        "was",
        "were",
        "with",
    }
)
_TOKEN_ALIASES = {
    "taught": "teach",
    "teacher": "teach",
    "teachers": "teach",
    "teaches": "teach",
    "teaching": "teach",
    "worked": "work",
    "working": "work",
    "works": "work",
    "children": "child",
    "families": "family",
    "studied": "study",
    "studies": "study",
}
_SINGULAR_PLACE_CLAIM_TOKENS = frozenset(
    {"born", "birthplace", "hometown", "origin"}
)


class MemoryValidationService:
    """Normalize and classify candidates without mutation or persistence."""

    def validate_candidate(
        self,
        candidate: MemoryCandidateCreate | Mapping[str, Any],
        *,
        legacy_id: int,
        existing_memories: Sequence[ExistingMemory],
        provenance_verifier: ProvenanceVerifier,
    ) -> MemoryValidationResult:
        """Return one structured, non-binding validation result."""
        normalized, issues = self._normalize_and_validate(candidate)
        if normalized is None:
            return self._result(
                status=MemoryValidationStatus.INVALID,
                action=MemoryValidationAction.REJECT_CANDIDATE,
                explanation=(
                    "The candidate does not satisfy the Memory Engine "
                    "structure and required-field contract."
                ),
                confidence="1.000",
                issues=issues,
            )

        provenance_issues: list[MemoryValidationIssue] = []
        for index, provenance in enumerate(normalized.provenance):
            provenance_issues.extend(
                provenance_verifier.verify(
                    legacy_id=legacy_id,
                    provenance=provenance,
                    provenance_index=index,
                )
            )
        if provenance_issues:
            return self._result(
                status=MemoryValidationStatus.INVALID,
                action=MemoryValidationAction.REJECT_CANDIDATE,
                explanation=(
                    "The candidate has provenance that cannot be verified "
                    "against the supplied legacy sources."
                ),
                confidence="1.000",
                normalized_candidate=normalized,
                issues=provenance_issues,
            )

        if self._is_insufficient(normalized):
            return self._result(
                status=MemoryValidationStatus.INSUFFICIENT_INFORMATION,
                action=MemoryValidationAction.REQUEST_MORE_INFORMATION,
                explanation=(
                    "The source-grounded candidate is too vague to preserve "
                    "as a useful standalone memory."
                ),
                confidence="0.900",
                normalized_candidate=normalized,
            )

        comparable = [
            memory
            for memory in existing_memories
            if (
                getattr(memory, "legacy_id", None) == legacy_id
                and self._review_status(memory)
                != MemoryReviewStatus.REJECTED.value
            )
        ]
        exact_ids = [
            memory.memory_id
            for memory in comparable
            if self._normalized_claim(memory.summary)
            == self._normalized_claim(normalized.summary)
        ]
        if exact_ids:
            return self._result(
                status=MemoryValidationStatus.DUPLICATE,
                action=MemoryValidationAction.DO_NOT_PERSIST,
                explanation=(
                    "The normalized claim already exists for this legacy."
                ),
                confidence="1.000",
                normalized_candidate=normalized,
                related_memory_ids=exact_ids,
            )

        contradiction_ids = [
            memory.memory_id
            for memory in comparable
            if self._is_contradiction(normalized, memory)
        ]
        if contradiction_ids:
            return self._result(
                status=MemoryValidationStatus.CONTRADICTION,
                action=MemoryValidationAction.REVIEW_CONTRADICTION,
                explanation=(
                    "The candidate conflicts with a preserved account. "
                    "Both accounts should remain available for human review."
                ),
                confidence="0.900",
                normalized_candidate=normalized,
                related_memory_ids=contradiction_ids,
            )

        enrichment_matches: list[tuple[int, float]] = []
        duplicate_matches: list[tuple[int, float]] = []
        for memory in comparable:
            score = self._similarity(
                normalized.summary,
                memory.summary,
            )
            same_category = (
                self._normalize_category(normalized.category)
                == self._normalize_category(memory.category)
            )
            subject_overlap = bool(
                self._candidate_subjects(normalized)
                & self._existing_subjects(memory)
            )
            if (
                same_category
                and subject_overlap
                and score >= 0.35
                and self._adds_information(normalized, memory)
            ):
                enrichment_matches.append((memory.memory_id, score))
            elif score >= 0.75:
                duplicate_matches.append((memory.memory_id, score))

        if enrichment_matches:
            best_score = max(score for _, score in enrichment_matches)
            return self._result(
                status=MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                action=MemoryValidationAction.REVIEW_ENRICHMENT,
                explanation=(
                    "The candidate appears related to an existing memory "
                    "and may add compatible detail. It was not merged."
                ),
                confidence=self._score_decimal(best_score),
                normalized_candidate=normalized,
                related_memory_ids=[
                    memory_id for memory_id, _ in enrichment_matches
                ],
            )

        if duplicate_matches:
            best_score = max(score for _, score in duplicate_matches)
            return self._result(
                status=MemoryValidationStatus.POSSIBLE_DUPLICATE,
                action=MemoryValidationAction.REVIEW_LINK,
                explanation=(
                    "The candidate is textually similar to an existing "
                    "memory and should be reviewed before persistence."
                ),
                confidence=self._score_decimal(best_score),
                normalized_candidate=normalized,
                related_memory_ids=[
                    memory_id for memory_id, _ in duplicate_matches
                ],
            )

        return self._result(
            status=MemoryValidationStatus.ACCEPTED,
            action=MemoryValidationAction.ACCEPT_CANDIDATE,
            explanation=(
                "The candidate is structurally valid, source-grounded, "
                "and has no deterministic conflict or duplicate match."
            ),
            confidence="0.800",
            normalized_candidate=normalized,
        )

    def validate_candidates(
        self,
        candidates: Sequence[
            MemoryCandidateCreate | Mapping[str, Any]
        ],
        *,
        legacy_id: int,
        existing_memories: Sequence[ExistingMemory],
        provenance_verifier: ProvenanceVerifier,
    ) -> list[MemoryValidationResult]:
        """Validate candidates independently without cross-candidate merging."""
        return [
            self.validate_candidate(
                candidate,
                legacy_id=legacy_id,
                existing_memories=existing_memories,
                provenance_verifier=provenance_verifier,
            )
            for candidate in candidates
        ]

    @classmethod
    def _normalize_and_validate(
        cls,
        candidate: MemoryCandidateCreate | Mapping[str, Any],
    ) -> tuple[
        MemoryCandidateCreate | None,
        list[MemoryValidationIssue],
    ]:
        try:
            if isinstance(candidate, BaseModel):
                raw = candidate.model_dump(mode="python")
            else:
                raw = copy.deepcopy(dict(candidate))
        except (TypeError, ValueError):
            return None, [
                MemoryValidationIssue(
                    code="invalid_structure",
                    message="The candidate must be an object.",
                )
            ]

        cls._normalize_candidate_values(raw)
        try:
            return MemoryCandidateCreate.model_validate(raw), []
        except ValidationError:
            return None, [
                MemoryValidationIssue(
                    code="invalid_structure",
                    message=(
                        "The candidate has missing, unsupported, or "
                        "out-of-range fields."
                    ),
                )
            ]

    @classmethod
    def _normalize_candidate_values(cls, raw: dict[str, Any]) -> None:
        for field in ("title", "summary", "emotional_significance"):
            if isinstance(raw.get(field), str):
                raw[field] = cls._normalize_display_text(raw[field])
        if isinstance(raw.get("uncertainty_note"), str):
            raw["uncertainty_note"] = cls._normalize_display_text(
                raw["uncertainty_note"]
            )
        if isinstance(raw.get("category"), str):
            raw["category"] = cls._normalize_category(raw["category"])

        tags = raw.get("tags")
        if isinstance(tags, list):
            normalized_tags: list[str] = []
            seen_tags: set[str] = set()
            for tag in tags:
                if not isinstance(tag, str):
                    normalized_tags.append(tag)
                    continue
                normalized = cls._collapse_space(tag).casefold()
                if normalized and normalized not in seen_tags:
                    normalized_tags.append(normalized)
                    seen_tags.add(normalized)
            raw["tags"] = normalized_tags

        participants = raw.get("participants")
        if isinstance(participants, list):
            for participant in participants:
                if not isinstance(participant, dict):
                    continue
                if isinstance(participant.get("name"), str):
                    participant["name"] = cls._collapse_space(
                        participant["name"]
                    )
                if isinstance(participant.get("relationship"), str):
                    participant["relationship"] = cls._normalize_category(
                        participant["relationship"]
                    )

        details = raw.get("details")
        if isinstance(details, dict):
            for place in details.get("places", []):
                if not isinstance(place, dict):
                    continue
                for field in ("name", "region", "country"):
                    if isinstance(place.get(field), str):
                        place[field] = cls._collapse_space(place[field])
            for temporal in details.get("temporal_references", []):
                if (
                    isinstance(temporal, dict)
                    and isinstance(temporal.get("text"), str)
                ):
                    temporal["text"] = cls._collapse_space(
                        temporal["text"]
                    )

    @classmethod
    def _normalize_display_text(cls, value: str) -> str:
        normalized = cls._collapse_space(value)
        normalized = _REPEATED_MARK_PATTERN.sub(r"\1", normalized)
        normalized = _LONG_ELLIPSIS_PATTERN.sub("...", normalized)
        if normalized:
            normalized = normalized[0].upper() + normalized[1:]
        return normalized

    @staticmethod
    def _collapse_space(value: str) -> str:
        return _SPACE_PATTERN.sub(
            " ",
            unicodedata.normalize("NFKC", value),
        ).strip()

    @classmethod
    def _normalize_category(cls, value: str) -> str:
        normalized = cls._collapse_space(value).casefold()
        return _CATEGORY_SEPARATOR_PATTERN.sub("_", normalized)

    @classmethod
    def _normalized_claim(cls, value: str) -> str:
        normalized = cls._normalize_display_text(value).casefold()
        return normalized.rstrip(" .!?;,")

    @classmethod
    def _tokens(cls, value: str) -> set[str]:
        tokens: set[str] = set()
        for token in _WORD_PATTERN.findall(value.casefold()):
            canonical = _TOKEN_ALIASES.get(token, token)
            if canonical not in _STOP_WORDS and len(canonical) > 1:
                tokens.add(canonical)
        return tokens

    @classmethod
    def _similarity(cls, first: str, second: str) -> float:
        first_tokens = cls._tokens(first)
        second_tokens = cls._tokens(second)
        if not first_tokens or not second_tokens:
            return 0.0
        return len(first_tokens & second_tokens) / len(
            first_tokens | second_tokens
        )

    @classmethod
    def _is_insufficient(cls, candidate: MemoryCandidateCreate) -> bool:
        meaningful_tokens = cls._tokens(candidate.summary)
        has_details = bool(
            candidate.details
            and (
                candidate.details.temporal_references
                or candidate.details.places
                or candidate.details.model_extra
            )
        )
        return (
            len(meaningful_tokens) < 3
            and not candidate.participants
            and not has_details
        )

    @classmethod
    def _is_contradiction(
        cls,
        candidate: MemoryCandidateCreate,
        existing: ExistingMemory,
    ) -> bool:
        if (
            cls._normalize_category(candidate.category)
            != cls._normalize_category(existing.category)
            or cls._has_uncertainty_candidate(candidate)
            or cls._has_uncertainty_existing(existing)
        ):
            return False

        candidate_subjects = cls._candidate_subjects(candidate)
        existing_subjects = cls._existing_subjects(existing)
        if (
            candidate_subjects
            and existing_subjects
            and not candidate_subjects.intersection(existing_subjects)
        ):
            return False

        candidate_years = cls._candidate_years(candidate)
        existing_years = cls._existing_years(existing)
        if (
            candidate_years
            and existing_years
            and candidate_years.isdisjoint(existing_years)
            and (
                cls._claim_skeleton(candidate.summary)
                == cls._claim_skeleton(existing.summary)
                or cls._similarity(
                    cls._claim_skeleton(candidate.summary),
                    cls._claim_skeleton(existing.summary),
                )
                >= 0.8
            )
        ):
            return True

        candidate_places = cls._candidate_places(candidate)
        existing_places = cls._existing_places(existing)
        claim_tokens = cls._tokens(candidate.summary) | cls._tokens(
            existing.summary
        )
        return (
            bool(claim_tokens & _SINGULAR_PLACE_CLAIM_TOKENS)
            and bool(candidate_places)
            and bool(existing_places)
            and candidate_places.isdisjoint(existing_places)
            and cls._place_claim_skeleton(
                candidate.summary,
                candidate_places,
            )
            == cls._place_claim_skeleton(
                existing.summary,
                existing_places,
            )
        )

    @classmethod
    def _adds_information(
        cls,
        candidate: MemoryCandidateCreate,
        existing: ExistingMemory,
    ) -> bool:
        candidate_tokens = cls._tokens(candidate.summary)
        existing_tokens = cls._tokens(existing.summary)
        if candidate_tokens - existing_tokens:
            return True
        if cls._candidate_years(candidate) - cls._existing_years(existing):
            return True
        if cls._candidate_places(candidate) - cls._existing_places(existing):
            return True
        existing_tags = cls._existing_tags(existing)
        return {
            tag.casefold() for tag in candidate.tags
        } - existing_tags != set()

    @classmethod
    def _candidate_subjects(
        cls,
        candidate: MemoryCandidateCreate,
    ) -> set[str]:
        subjects = {
            cls._normalized_claim(participant.name)
            for participant in candidate.participants
            if participant.role in {None, "subject"}
        }
        if subjects:
            return subjects
        return cls._identity_tokens(candidate.summary)

    @classmethod
    def _existing_subjects(cls, existing: ExistingMemory) -> set[str]:
        participants = getattr(existing, "participants", None) or []
        subjects = {
            cls._normalized_claim(participant.name)
            for participant in participants
            if getattr(participant, "role", None) in {None, "subject"}
        }
        if subjects:
            return subjects
        return cls._identity_tokens(existing.summary)

    @classmethod
    def _identity_tokens(cls, summary: str) -> set[str]:
        return {
            token
            for token in cls._tokens(summary)
            if token in {"mom", "mother", "dad", "father", "grandma",
                         "grandmother", "grandpa", "grandfather",
                         "partner", "spouse"}
        }

    @staticmethod
    def _details_dict(details: object) -> dict:
        if isinstance(details, BaseModel):
            return details.model_dump(mode="python")
        return details if isinstance(details, dict) else {}

    @classmethod
    def _candidate_years(
        cls,
        candidate: MemoryCandidateCreate,
    ) -> set[str]:
        values = set(_YEAR_PATTERN.findall(candidate.summary))
        details = cls._details_dict(candidate.details)
        for temporal in details.get("temporal_references", []):
            if isinstance(temporal, dict):
                for value in (
                    temporal.get("text"),
                    temporal.get("start_date"),
                    temporal.get("end_date"),
                ):
                    if isinstance(value, str):
                        values.update(_YEAR_PATTERN.findall(value))
        return values

    @classmethod
    def _existing_years(cls, existing: ExistingMemory) -> set[str]:
        values = set(_YEAR_PATTERN.findall(existing.summary))
        details = cls._details_dict(getattr(existing, "details", None))
        for temporal in details.get("temporal_references", []):
            if isinstance(temporal, dict):
                for value in (
                    temporal.get("text"),
                    temporal.get("start_date"),
                    temporal.get("end_date"),
                ):
                    if isinstance(value, str):
                        values.update(_YEAR_PATTERN.findall(value))
        return values

    @classmethod
    def _candidate_places(
        cls,
        candidate: MemoryCandidateCreate,
    ) -> set[str]:
        details = cls._details_dict(candidate.details)
        return cls._place_names(details)

    @classmethod
    def _existing_places(cls, existing: ExistingMemory) -> set[str]:
        return cls._place_names(
            cls._details_dict(getattr(existing, "details", None))
        )

    @classmethod
    def _place_names(cls, details: dict) -> set[str]:
        return {
            cls._normalized_claim(place["name"])
            for place in details.get("places", [])
            if (
                isinstance(place, dict)
                and isinstance(place.get("name"), str)
                and place["name"].strip()
            )
        }

    @classmethod
    def _has_uncertainty_candidate(
        cls,
        candidate: MemoryCandidateCreate,
    ) -> bool:
        return bool(candidate.uncertainty_note) or cls._details_uncertain(
            cls._details_dict(candidate.details)
        )

    @classmethod
    def _has_uncertainty_existing(cls, existing: ExistingMemory) -> bool:
        return bool(
            getattr(existing, "uncertainty_note", None)
        ) or cls._details_uncertain(
            cls._details_dict(getattr(existing, "details", None))
        )

    @staticmethod
    def _details_uncertain(details: dict) -> bool:
        uncertain_values = {"approximate", "uncertain", "disputed", "possible"}
        for item in [
            *details.get("temporal_references", []),
            *details.get("places", []),
        ]:
            if not isinstance(item, dict):
                continue
            if (
                item.get("is_approximate") is True
                or item.get("certainty") in uncertain_values
            ):
                return True
        return False

    @classmethod
    def _claim_skeleton(cls, summary: str) -> str:
        return cls._normalized_claim(
            _YEAR_PATTERN.sub("<year>", summary)
        )

    @classmethod
    def _place_claim_skeleton(
        cls,
        summary: str,
        places: set[str],
    ) -> str:
        skeleton = cls._normalized_claim(summary)
        for place in sorted(places, key=len, reverse=True):
            skeleton = re.sub(
                rf"\b{re.escape(place)}\b",
                "<place>",
                skeleton,
                flags=re.IGNORECASE,
            )
        return skeleton

    @classmethod
    def _existing_tags(cls, existing: ExistingMemory) -> set[str]:
        links = getattr(existing, "tag_links", None) or []
        names: set[str] = set()
        for link in links:
            tag = getattr(link, "tag", None)
            name = getattr(tag, "name", None)
            if isinstance(name, str):
                names.add(name.casefold())
        return names

    @staticmethod
    def _review_status(existing: ExistingMemory) -> str:
        status = getattr(existing, "review_status", None)
        value = getattr(status, "value", status)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _score_decimal(score: float) -> str:
        bounded = min(1.0, max(0.0, score))
        return f"{bounded:.3f}"

    @staticmethod
    def _result(
        *,
        status: MemoryValidationStatus,
        action: MemoryValidationAction,
        explanation: str,
        confidence: str,
        normalized_candidate: MemoryCandidateCreate | None = None,
        related_memory_ids: list[int] | None = None,
        issues: list[MemoryValidationIssue] | None = None,
    ) -> MemoryValidationResult:
        return MemoryValidationResult(
            status=status,
            recommended_action=action,
            explanation=explanation,
            validation_confidence=Decimal(confidence),
            normalized_candidate=normalized_candidate,
            related_memory_ids=related_memory_ids or [],
            issues=issues or [],
        )
