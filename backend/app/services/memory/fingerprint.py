"""Deterministic, non-identifying fingerprints for exact memory idempotency."""

import hashlib
import json
import re
import unicodedata

from app.schemas.memory import MemoryCandidateCreate


_SPACE_PATTERN = re.compile(r"\s+")
_TRAILING_PUNCTUATION = re.compile(r"[!?,.;:]+$")


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _SPACE_PATTERN.sub(" ", text).strip().casefold()
    return _TRAILING_PUNCTUATION.sub("", text)


def build_memory_fingerprint(
    legacy_id: int,
    candidate: MemoryCandidateCreate,
) -> str:
    """Return a stable SHA-256 aid; it is not a Memory primary key."""
    temporal = []
    if candidate.details is not None:
        temporal = [
            {
                "text": _normalize(item.text),
                "start": item.start_date,
                "end": item.end_date,
                "approximate": item.is_approximate,
            }
            for item in candidate.details.temporal_references
        ]
    participants = sorted(
        (
            _normalize(item.name),
            _normalize(item.relationship),
            _normalize(item.role),
        )
        for item in candidate.participants
    )
    payload = {
        "legacy_id": legacy_id,
        "memory_type": candidate.memory_type.value,
        "category": _normalize(candidate.category),
        "summary": _normalize(candidate.summary),
        "participants": participants,
        "temporal": temporal,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
