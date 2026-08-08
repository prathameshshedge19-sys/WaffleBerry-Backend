"""Source-agnostic provenance verification for candidate validation."""

import json
from dataclasses import dataclass
from typing import Protocol

from app.schemas.memory import MemoryProvenanceCreate
from app.services.memory.validation_contracts import MemoryValidationIssue


@dataclass(frozen=True, slots=True)
class ProvenanceSourceRecord:
    """One trusted source registered by a source adapter."""

    source_type: str
    legacy_id: int
    speaker: str
    content: str
    conversation_id: int | None = None
    message_id: int | None = None
    story_session_id: int | None = None
    story_message_id: int | None = None
    source_locator: dict | None = None


class ProvenanceVerifier(Protocol):
    """Interface future text and media source adapters can implement."""

    def verify(
        self,
        *,
        legacy_id: int,
        provenance: MemoryProvenanceCreate,
        provenance_index: int,
    ) -> list[MemoryValidationIssue]:
        """Return safe issues; an empty list means provenance is valid."""
        ...


class RegisteredProvenanceVerifier:
    """Verify provenance against trusted, application-loaded source records."""

    def __init__(
        self,
        source_records: list[ProvenanceSourceRecord],
    ) -> None:
        self._records: dict[tuple, ProvenanceSourceRecord] = {}
        for record in source_records:
            key = self._record_key(record)
            if key in self._records:
                raise ValueError(
                    "Duplicate provenance source registration."
                )
            self._records[key] = record

    def verify(
        self,
        *,
        legacy_id: int,
        provenance: MemoryProvenanceCreate,
        provenance_index: int,
    ) -> list[MemoryValidationIssue]:
        """Verify identity, legacy, speaker, and exact source excerpt."""
        key = self._provenance_key(provenance)
        if key is None:
            return [
                self._issue(
                    "invalid_source_reference",
                    "The provenance source identifiers are incomplete.",
                    provenance_index,
                )
            ]

        record = self._records.get(key)
        if record is None:
            return [
                self._issue(
                    "missing_source",
                    "The referenced provenance source does not exist.",
                    provenance_index,
                )
            ]
        if record.legacy_id != legacy_id:
            return [
                self._issue(
                    "cross_legacy_source",
                    "The provenance source belongs to another legacy.",
                    provenance_index,
                )
            ]

        record_speaker = record.speaker.strip().casefold()
        claimed_speaker = (
            provenance.speaker.strip().casefold()
            if provenance.speaker
            else ""
        )
        if (
            record_speaker == "assistant"
            or claimed_speaker == "assistant"
        ):
            return [
                self._issue(
                    "assistant_source",
                    "Assistant responses cannot support an extracted memory.",
                    provenance_index,
                )
            ]
        if not record_speaker or claimed_speaker != record_speaker:
            return [
                self._issue(
                    "speaker_mismatch",
                    "The provenance speaker does not match the source.",
                    provenance_index,
                )
            ]
        if (
            provenance.source_type
            in {"conversation", "story_session"}
            and record_speaker != "user"
        ):
            return [
                self._issue(
                    "invalid_text_speaker",
                    "Text conversation memories require a user-authored "
                    "source message.",
                    provenance_index,
                )
            ]
        if (
            not provenance.excerpt
            or provenance.excerpt not in record.content
        ):
            return [
                self._issue(
                    "fabricated_excerpt",
                    "The provenance excerpt is not present in the "
                    "referenced source.",
                    provenance_index,
                )
            ]
        return []

    @staticmethod
    def _issue(
        code: str,
        message: str,
        provenance_index: int,
    ) -> MemoryValidationIssue:
        return MemoryValidationIssue(
            code=code,
            message=message,
            provenance_index=provenance_index,
        )

    @classmethod
    def _record_key(cls, record: ProvenanceSourceRecord) -> tuple:
        if record.source_type == "conversation":
            return (
                "conversation",
                record.conversation_id,
                record.message_id,
            )
        if record.source_type == "story_session":
            return (
                "story_session",
                record.story_session_id,
                record.story_message_id,
            )
        locator = cls._locator_key(record.source_locator)
        if locator is None:
            raise ValueError(
                "Non-text provenance sources require a source locator."
            )
        return (record.source_type, locator)

    @classmethod
    def _provenance_key(
        cls,
        provenance: MemoryProvenanceCreate,
    ) -> tuple | None:
        if provenance.source_type == "conversation":
            if (
                provenance.conversation_id is None
                or provenance.message_id is None
            ):
                return None
            return (
                "conversation",
                provenance.conversation_id,
                provenance.message_id,
            )
        if provenance.source_type == "story_session":
            if (
                provenance.story_session_id is None
                or provenance.story_message_id is None
            ):
                return None
            return (
                "story_session",
                provenance.story_session_id,
                provenance.story_message_id,
            )
        locator = cls._locator_key(provenance.source_locator)
        if locator is None:
            return None
        return (provenance.source_type, locator)

    @staticmethod
    def _locator_key(locator: dict | None) -> str | None:
        if not locator:
            return None
        return json.dumps(
            locator,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
