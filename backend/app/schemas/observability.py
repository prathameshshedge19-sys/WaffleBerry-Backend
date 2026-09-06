"""Allowlisted telemetry vocabulary. No content-bearing request/result fields."""
from dataclasses import dataclass, field
from typing import Literal

ERRORS = frozenset({
    'authorization_denied', 'access_changed', 'scope_invalid', 'turn_conflict',
    'turn_already_processing', 'provider_connection', 'provider_timeout', 'provider_failed',
    'memory_retrieval_failed', 'personality_unavailable', 'current_info_unavailable',
    'tool_invalid_arguments', 'tool_not_allowed', 'persistence_failed',
    'post_processing_failed', 'cancelled', 'unknown_internal',
})
DIMENSIONS = {
    'mode': frozenset({'rya', 'legacy', 'unknown'}),
    'role': frozenset({'owner', 'collaborator', 'viewer', 'unknown'}),
    'route': frozenset({'personal', 'general', 'mixed', 'fresh', 'unknown'}),
    'input_mode': frozenset({'text', 'voice', 'realtime_voice', 'unknown'}),
    'outcome': frozenset({'completed', 'failed', 'interrupted', 'replayed', 'unknown'}),
    'personality_status': frozenset({'available', 'fallback'}),
    'tool_name': frozenset({'retrieve_legacy_memories', 'get_legacy_personality',
                           'get_visitor_relationship_context', 'get_current_information', 'unknown'}),
    'provider_kind': frozenset({'text', 'embedding', 'stt', 'tts', 'web', 'realtime'}),
}
STAGES = frozenset({
    'preparation', 'history_loading', 'classification', 'memory_analysis', 'memory_retrieval',
    'personality_selection', 'relationship_context', 'current_information', 'provider_generation',
    'assistant_persistence', 'post_turn_effects', 'memory_effect', 'activity_effect',
    'personality_invalidation', 'tool', 'voice_provider',
})


@dataclass(frozen=True)
class UsageValue:
    value: int | float | None = None
    status: Literal['measured', 'estimated', 'unavailable'] = 'unavailable'


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: UsageValue = field(default_factory=UsageValue)
    output_tokens: UsageValue = field(default_factory=UsageValue)
    cached_input_tokens: UsageValue = field(default_factory=UsageValue)
    duration_seconds: UsageValue = field(default_factory=UsageValue)
    characters: UsageValue = field(default_factory=UsageValue)
    audio_input_tokens: UsageValue = field(default_factory=UsageValue)
    audio_output_tokens: UsageValue = field(default_factory=UsageValue)
    tool_calls: UsageValue = field(default_factory=UsageValue)
    model: str | None = None


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    attempt_id: str
    kind: str
    outcome: str
    usage: ProviderUsage
    correlation: dict = field(repr=False)
    provider_request_hash: str | None = None
    cache_hit: bool = False
    request_count: int = 1
    request_count_status: Literal['measured'] = 'measured'
