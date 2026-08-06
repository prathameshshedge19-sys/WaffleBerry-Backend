"""Extract validated, source-traceable memory candidates without persistence."""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import ValidationError

from app.models.memory import Legacy, StoryMessage, StorySession
from app.models.user import Conversation, Message
from app.schemas.memory import MemoryCandidateCreate, MemoryProvenanceCreate
from app.services.ai.ai_service import AIService
from app.services.ai.prompt_builder import PromptBuilder
from app.services.ai.provider import AIMessage
from app.services.memory.contracts import (
    ExtractedMemory,
    MemoryExtractionResult,
)
from app.services.memory.exceptions import (
    MemoryExtractionResponseError,
    MemoryExtractionSourceError,
)


MEMORY_EXTRACTOR_VERSION = "memory-extractor-v1"
ExtractionSourceType = Literal["story_session", "conversation"]
logger = logging.getLogger(__name__)

_EDUCATION_PROFESSION_TERMS = frozenset(
    {"educator", "professor", "teacher", "tutor"}
)

_NULLABLE_STRING = {"type": ["string", "null"]}
MEMORY_EXTRACTION_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "memories": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["atomic", "narrative"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "personal_detail", "relationship", "place",
                            "life_event", "preference", "tradition",
                            "habit", "value", "achievement", "challenge",
                            "lesson", "expression", "story",
                        ],
                    },
                    "title": {"type": "string", "minLength": 1, "maxLength": 255},
                    "summary": {"type": "string", "minLength": 1},
                    "details": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "temporal_references": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "text": {"type": "string", "minLength": 1, "maxLength": 255},
                                        "start_date": {"type": ["string", "null"], "maxLength": 32},
                                        "end_date": {"type": ["string", "null"], "maxLength": 32},
                                        "precision": {"type": "string", "enum": ["day", "month", "season", "year", "decade", "range", "unknown"]},
                                        "is_approximate": {"type": "boolean"},
                                        "certainty": {"type": "string", "enum": ["stated", "approximate", "uncertain", "disputed"]},
                                    },
                                    "required": ["text", "start_date", "end_date", "precision", "is_approximate", "certainty"],
                                },
                            },
                            "places": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string", "minLength": 1, "maxLength": 255},
                                        "region": {"type": ["string", "null"], "maxLength": 255},
                                        "country": {"type": ["string", "null"], "maxLength": 255},
                                        "certainty": {"type": "string", "enum": ["stated", "approximate", "uncertain", "disputed", "possible"]},
                                    },
                                    "required": ["name", "region", "country", "certainty"],
                                },
                            },
                            "semantic_attributes": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "profession": _NULLABLE_STRING,
                                    "occupation_category": _NULLABLE_STRING,
                                    "taught_relationship": _NULLABLE_STRING,
                                    "education_level": _NULLABLE_STRING,
                                    "birthplace": _NULLABLE_STRING,
                                },
                                "required": [
                                    "profession",
                                    "occupation_category",
                                    "taught_relationship",
                                    "education_level",
                                    "birthplace",
                                ],
                            },
                            "identity_facts": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "fact_type": {"type": "string", "enum": ["full_name", "preferred_name", "spouse_name", "child_name", "parent_name", "sibling_name", "birth_date", "birthplace", "hometown", "occupation", "education"]},
                                        "value": {"type": "string", "minLength": 1, "maxLength": 255},
                                        "relationship": _NULLABLE_STRING,
                                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                        "uncertainty_note": _NULLABLE_STRING,
                                    },
                                    "required": ["fact_type", "value", "relationship", "confidence", "uncertainty_note"],
                                },
                            },
                        },
                        "required": [
                            "temporal_references",
                            "places",
                            "semantic_attributes",
                            "identity_facts",
                        ],
                    },
                    "emotional_significance": _NULLABLE_STRING,
                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                    "extraction_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertainty_note": _NULLABLE_STRING,
                    "participants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 255},
                                "relationship": {"type": ["string", "null"], "maxLength": 100},
                                "role": {"type": ["string", "null"], "enum": ["subject", "witness", "mentioned_person", None]},
                            },
                            "required": ["name", "relationship", "role"],
                        },
                    },
                    "tags": {"type": "array", "maxItems": 30, "items": {"type": "string", "minLength": 1, "maxLength": 80}},
                    "evidence": {
                        "type": "array", "minItems": 1, "maxItems": 20,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {
                                "source_message_id": {"type": "integer", "minimum": 1},
                                "excerpt": {"type": "string", "minLength": 1, "maxLength": 600},
                            },
                            "required": ["source_message_id", "excerpt"],
                        },
                    },
                },
                "required": ["memory_type", "category", "title", "summary", "details", "emotional_significance", "importance", "extraction_confidence", "uncertainty_note", "participants", "tags", "evidence"],
            },
        }
    },
    "required": ["memories"],
}


class ExtractableMessage(Protocol):
    """Application-visible source fields used by extraction."""

    role: object
    content: str


@dataclass(frozen=True, slots=True)
class _SourceMessage:
    """Validated source message prepared for the model and evidence checks."""

    source_message_id: int
    role: Literal["user", "assistant"]
    content: str


class MemoryExtractionService:
    """Convert persisted source messages into unpersisted memory candidates."""

    def __init__(
        self,
        ai_service: AIService,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._ai_service = ai_service
        self._prompt_builder = prompt_builder or PromptBuilder()

    async def extract_story_session(
        self,
        legacy: Legacy,
        story_session: StorySession,
        messages: Sequence[StoryMessage],
    ) -> list[MemoryCandidateCreate]:
        """Extract candidates from one persisted, same-legacy Story Session."""
        if (
            legacy.legacy_id is None
            or story_session.story_session_id is None
            or story_session.legacy_id != legacy.legacy_id
        ):
            raise MemoryExtractionSourceError(
                "Story Session does not belong to the supplied legacy."
            )

        prepared = self._prepare_messages(
            messages,
            id_attribute="story_message_id",
            container_attribute="story_session_id",
            container_id=story_session.story_session_id,
            order_attribute="sequence",
        )
        return await self._extract(
            legacy=legacy,
            source_type="story_session",
            source_container_id=story_session.story_session_id,
            chapter=story_session.chapter_key,
            messages=prepared,
        )

    async def extract_conversation(
        self,
        legacy: Legacy,
        conversation: Conversation,
        messages: Sequence[Message],
    ) -> list[MemoryCandidateCreate]:
        """Extract candidates from one persisted, same-legacy conversation."""
        if (
            legacy.legacy_id is None
            or conversation.conversation_id is None
            or conversation.legacy_id != legacy.legacy_id
        ):
            raise MemoryExtractionSourceError(
                "Conversation does not belong to the supplied legacy."
            )

        prepared = self._prepare_messages(
            messages,
            id_attribute="message_id",
            container_attribute="conversation_id",
            container_id=conversation.conversation_id,
            order_attribute="message_id",
        )
        return await self._extract(
            legacy=legacy,
            source_type="conversation",
            source_container_id=conversation.conversation_id,
            chapter=None,
            messages=prepared,
        )

    async def _extract(
        self,
        *,
        legacy: Legacy,
        source_type: ExtractionSourceType,
        source_container_id: int,
        chapter: str | None,
        messages: list[_SourceMessage],
    ) -> list[MemoryCandidateCreate]:
        """Call the shared AI service, parse output, and build provenance."""
        eligible_messages = {
            message.source_message_id: message
            for message in messages
            if message.role == "user"
        }
        if not eligible_messages:
            return []

        request_payload = {
            "legacy_context": {
                "display_name": legacy.display_name,
                "relationship": legacy.relationship,
                "chapter": chapter,
            },
            "source": {
                "source_type": source_type,
                "source_container_id": source_container_id,
                "messages": [
                    {
                        "source_message_id": message.source_message_id,
                        "role": message.role,
                        "content": message.content,
                        "eligible_as_evidence": message.role == "user",
                    }
                    for message in messages
                ],
            },
            "output_contract": MemoryExtractionResult.model_json_schema(),
        }
        ai_messages = [
            AIMessage(
                role="system",
                content=(
                    self._prompt_builder
                    .build_memory_extraction_system_prompt()
                ),
            ),
            AIMessage(
                role="user",
                content=json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        ]
        response_text = await self._ai_service.generate_response(
            ai_messages,
            structured_response_schema=MEMORY_EXTRACTION_RESPONSE_SCHEMA,
        )
        result = self._parse_response(response_text)

        candidates = []
        for index, extracted in enumerate(result.memories):
            try:
                candidates.append(
                    self._build_candidate(
                        extracted,
                        source_type=source_type,
                        source_container_id=source_container_id,
                        chapter=chapter,
                        eligible_messages=eligible_messages,
                    )
                )
            except MemoryExtractionResponseError as exc:
                logger.warning(
                    "memory_extraction_candidate_skipped",
                    extra={
                        "candidate_index": index,
                        "exception_type": type(exc).__name__,
                    },
                )
        logger.info(
            "memory_extraction_candidates_built",
            extra={
                "source_type": source_type,
                "source_container_id": source_container_id,
                "candidate_count": len(result.memories),
                "persistable_candidate_count": len(candidates),
                "skipped_candidate_count": len(result.memories) - len(candidates),
            },
        )
        return candidates

    @staticmethod
    def _prepare_messages(
        messages: Sequence[ExtractableMessage],
        *,
        id_attribute: str,
        container_attribute: str,
        container_id: int,
        order_attribute: str,
    ) -> list[_SourceMessage]:
        """Validate IDs, ownership container, content, role, and source order."""
        prepared: list[tuple[int, int, _SourceMessage]] = []
        seen_ids: set[int] = set()
        for message in messages:
            message_id = getattr(message, id_attribute, None)
            order_value = getattr(message, order_attribute, None)
            message_container_id = getattr(
                message,
                container_attribute,
                None,
            )
            role = getattr(getattr(message, "role", None), "value", None)
            if role is None:
                role = getattr(message, "role", None)
            content = getattr(message, "content", None)

            if (
                not isinstance(message_id, int)
                or message_id <= 0
                or not isinstance(order_value, int)
                or order_value <= 0
                or message_id in seen_ids
                or message_container_id != container_id
            ):
                raise MemoryExtractionSourceError(
                    "Source messages must be persisted, unique, and belong "
                    "to the supplied source container."
                )
            seen_ids.add(message_id)
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                raise MemoryExtractionSourceError(
                    "Source message content must not be blank."
                )

            prepared.append(
                (
                    order_value,
                    message_id,
                    _SourceMessage(
                        source_message_id=message_id,
                        role=role,
                        content=content,
                    ),
                )
            )
        prepared.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in prepared]

    @staticmethod
    def _parse_response(response_text: str) -> MemoryExtractionResult:
        """Parse strict JSON, tolerating only one surrounding JSON fence."""
        normalized = response_text.strip()
        had_code_fence = normalized.startswith("```")
        if normalized.startswith("```") and normalized.endswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[0].strip() in {"```", "```json"}:
                normalized = "\n".join(lines[1:-1]).strip()

        try:
            payload = json.loads(normalized)
            return MemoryExtractionResult.model_validate(payload)
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            validation_errors = exc.errors() if isinstance(exc, ValidationError) else []
            logger.warning(
                "Invalid memory extraction response.",
                extra={
                    "exception_type": type(exc).__name__,
                    "validation_field_paths": [
                        ".".join(str(part) for part in error["loc"])
                        for error in validation_errors
                    ],
                    "validation_error_types": [
                        error["type"] for error in validation_errors
                    ],
                    "response_character_count": len(response_text),
                    "response_had_code_fence": had_code_fence,
                },
            )
            raise MemoryExtractionResponseError(
                "Memory extraction returned an invalid structured response."
            ) from None

    @staticmethod
    def _build_candidate(
        extracted: ExtractedMemory,
        *,
        source_type: ExtractionSourceType,
        source_container_id: int,
        chapter: str | None,
        eligible_messages: dict[int, _SourceMessage],
    ) -> MemoryCandidateCreate:
        """Attach server-verified provenance to one model-produced candidate."""
        provenance: list[MemoryProvenanceCreate] = []
        seen_evidence: set[tuple[int, str]] = set()
        for evidence in extracted.evidence:
            source_message = eligible_messages.get(
                evidence.source_message_id
            )
            key = (evidence.source_message_id, evidence.excerpt)
            if source_message is None or evidence.excerpt not in source_message.content:
                raise MemoryExtractionResponseError(
                    "Memory extraction cited invalid source evidence."
                )
            if key in seen_evidence:
                continue
            seen_evidence.add(key)

            source_values = {
                "source_type": source_type,
                "excerpt": evidence.excerpt,
                "speaker": "user",
                "chapter": chapter,
                "extractor_version": MEMORY_EXTRACTOR_VERSION,
            }
            if source_type == "story_session":
                source_values.update(
                    {
                        "story_session_id": source_container_id,
                        "story_message_id": evidence.source_message_id,
                    }
                )
            else:
                source_values.update(
                    {
                        "conversation_id": source_container_id,
                        "message_id": evidence.source_message_id,
                    }
                )
            provenance.append(MemoryProvenanceCreate(**source_values))

        if not provenance:
            raise MemoryExtractionResponseError(
                "Memory extraction did not provide usable source evidence."
            )

        extracted = MemoryExtractionService._sanitize_semantic_attributes(
            extracted
        )

        return MemoryCandidateCreate(
            memory_type=extracted.memory_type,
            category=extracted.category,
            title=extracted.title,
            summary=extracted.summary,
            details=extracted.details,
            emotional_significance=extracted.emotional_significance,
            importance=extracted.importance,
            extraction_confidence=extracted.extraction_confidence,
            uncertainty_note=extracted.uncertainty_note,
            participants=extracted.participants,
            tags=extracted.tags,
            provenance=provenance,
        )

    @staticmethod
    def _sanitize_semantic_attributes(
        extracted: ExtractedMemory,
    ) -> ExtractedMemory:
        """Null unsupported optional attributes without losing the memory."""
        attributes = extracted.details.semantic_attributes
        evidence_text = " ".join(
            evidence.excerpt for evidence in extracted.evidence
        ).casefold()
        unsupported: set[str] = set()
        for field in (
            "profession",
            "taught_relationship",
            "education_level",
            "birthplace",
        ):
            value = getattr(attributes, field)
            if value is not None and value.casefold() not in evidence_text:
                unsupported.add(field)

        category = attributes.occupation_category
        if category is not None and category.casefold() not in evidence_text:
            profession_tokens = set(
                attributes.profession.casefold().split()
                if attributes.profession is not None
                else ()
            )
            if not (
                category.casefold() == "education"
                and profession_tokens & _EDUCATION_PROFESSION_TERMS
                and "profession" not in unsupported
            ):
                unsupported.add("occupation_category")
        if not unsupported:
            return extracted
        sanitized_attributes = attributes.model_copy(
            update={field: None for field in unsupported}
        )
        sanitized_details = extracted.details.model_copy(
            update={"semantic_attributes": sanitized_attributes}
        )
        logger.info(
            "memory_extraction_semantic_attributes_sanitized",
            extra={"sanitized_attribute_fields": sorted(unsupported)},
        )
        return extracted.model_copy(update={"details": sanitized_details})
