"""Allowlisted operational events: never accept payloads, keys, IDs or errors."""
import json
import logging

_EVENTS = frozenset({
    "worker_starting", "worker_ready", "worker_not_ready", "worker_stopping",
    "manifest_failure", "job_admitted", "admission_rejected", "job_completed",
    "job_failed", "job_cancelled", "stale_publication_blocked",
    "purge_queued", "purge_completed", "fallback", "lease_recovered",
})
_REASONS = frozenset({
    "capacity", "unavailable", "timeout", "shutdown", "invalid_output",
    "storage", "manifest", "stale", "provider", "recovery", "disabled",
})
_logger = logging.getLogger("legarya.voice")


def emit(event, *, reason=None):
    if event not in _EVENTS:
        return
    record = {"event": "voice_" + event}
    if reason in _REASONS:
        record["reason"] = reason
    _logger.info(json.dumps(record, separators=(",", ":")))
