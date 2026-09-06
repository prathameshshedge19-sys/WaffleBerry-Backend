"""Public management projection: no prompts, fingerprints, or worker internals."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ObservationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(max_length=600)
    dimension: str = Field(max_length=64)
    confidence: Literal["Well supported", "Supported", "Tentative"]
    context: str | None = Field(default=None, max_length=600)
    conflicting_accounts: bool = False
    supporting_memory_ids: list[int]
    supporting_memory_count: int


class ExpressionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expression: str = Field(max_length=240)
    context: str = Field(max_length=620)
    language: str = Field(max_length=64)
    supporting_memory_ids: list[int]
    supporting_memory_count: int


class PersonalityDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legacy_id: int = Field(gt=0)
    status: Literal["ready", "rebuilding", "unavailable", "failed", "stale"]
    observations: list[ObservationSummary] = Field(default_factory=list)
    signature_expressions: list[ExpressionSummary] = Field(default_factory=list)
