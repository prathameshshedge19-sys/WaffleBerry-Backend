"""Provider-neutral, model-visible task arguments. Security scope is never input."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ARGUMENT_BYTES = 4096
MAX_QUERY_CHARS = 512
MAX_MEMORIES = 5
MAX_TIMELINE_EVENTS = 8
MAX_CUES = 5
MAX_SOURCES = 3
MAX_RESULT_BYTES = 8192

ToolErrorCode = Literal[
    "tool_not_allowed", "tool_invalid_arguments", "tool_scope_invalid",
    "tool_data_unavailable", "tool_current_info_unavailable", "tool_internal_error",
]


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class QueryArguments(EmptyArguments):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value):
        if not isinstance(value, str) or len(value) > MAX_QUERY_CHARS:
            raise ValueError("Invalid bounded query")
        value = " ".join(value.split())
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid query")
        return value


class MemoryArguments(QueryArguments):
    max_results: int = Field(default=MAX_MEMORIES, ge=1, le=MAX_MEMORIES)


class TimelineArguments(QueryArguments):
    max_results: int = Field(default=MAX_TIMELINE_EVENTS, ge=1, le=MAX_TIMELINE_EVENTS)
