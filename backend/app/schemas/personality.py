"""Bounded evidence data, never instructions or authoritative personal facts."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveID = Annotated[int, Field(strict=True, gt=0)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=600)]
Dimension = Literal["directness", "sociability", "warmth", "humor", "deliberateness", "value"]
EvidenceType = Literal["explicit_description", "quotation", "habitual_account", "behavioral_inference"]
Confidence = Literal["tentative", "supported", "corroborated"]


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSpan(DataModel):
    field: Literal["canonical_text", "source_excerpt"]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: ShortText

    @model_validator(mode="after")
    def valid_offsets(self):
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("Invalid evidence span")
        return self


class EvidenceRef(DataModel):
    memory_id: PositiveID
    content_fingerprint: Fingerprint
    independence_key: Fingerprint
    span: SourceSpan


class EvidenceContext(DataModel):
    qualification: ShortText | None = None
    relationship: Annotated[str, Field(max_length=160)] | None = None
    entity_ids: tuple[PositiveID, ...] = Field(default=(), max_length=16)
    setting: Annotated[str, Field(max_length=160)] | None = None
    topic: Annotated[str, Field(max_length=160)] | None = None
    time: Annotated[str, Field(max_length=160)] | None = None


class PersonalityObservation(DataModel):
    dimension: Dimension
    trait: Annotated[str, Field(min_length=1, max_length=64)]
    description: ShortText
    evidence_type: EvidenceType
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=8)
    context: EvidenceContext = Field(default_factory=EvidenceContext)
    confidence: Confidence
    confidence_reason: ShortText
    conflict_memory_ids: tuple[PositiveID, ...] = Field(default=(), max_length=64)
    response_style_eligible: bool = False


class SignatureExpression(DataModel):
    expression: Annotated[str, Field(min_length=1, max_length=240)]
    original_language: Annotated[str, Field(min_length=1, max_length=64)]
    original_script: Literal["Devanagari", "Latin", "Other", "Mixed"]
    meaning: ShortText | None = None
    context: EvidenceContext = Field(default_factory=EvidenceContext)
    reported_frequency: Literal["always", "often", "sometimes", "once", "unspecified"]
    confidence: Confidence
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=8)
    response_style_eligible: bool = False


class PersonalityProfile(DataModel):
    legacy_id: PositiveID
    source_generation: int = Field(ge=1)
    schema_version: Literal[1] = 1
    policy_version: Literal["l13-conservative-v1"] = "l13-conservative-v1"
    builder_id: Literal["deterministic-evidence-v1"] = "deterministic-evidence-v1"
    observations: tuple[PersonalityObservation, ...] = Field(default=(), max_length=48)
    signature_expressions: tuple[SignatureExpression, ...] = Field(default=(), max_length=16)
    evidence_manifest: dict[PositiveID, Fingerprint] = Field(default_factory=dict, max_length=2048)

    @field_validator("evidence_manifest", mode="before")
    @classmethod
    def normalize_manifest_keys(cls, value):
        """Restore integer IDs from JSON object keys, without coercing evidence."""
        if not isinstance(value, dict):
            return value
        normalized = {}
        for key, fingerprint in value.items():
            if isinstance(key, str):
                if re.fullmatch(r"[1-9][0-9]*", key) is None:
                    raise ValueError("Manifest keys must be canonical positive integer IDs")
                key = int(key)
            if key in normalized:
                raise ValueError("Duplicate normalized manifest ID")
            normalized[key] = fingerprint
        return normalized
