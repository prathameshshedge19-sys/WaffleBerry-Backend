"""Read-only, capability-filtered provenance for existing canonical memories."""
import hashlib
from sqlalchemy import select
from app.models.media_intelligence import MemorySourceLink, SourceEvidence
from app.models.media_source import MediaSource
from app.models.memory import Memory
from app.services.authorization import legacy_role


def memory_provenance(db, legacy, user_id, memory_ids):
    result = {key: [] for key in memory_ids}
    if not memory_ids:
        return result
    owner = legacy_role(db, user_id, legacy) == "owner"
    rows = db.execute(select(MemorySourceLink, MediaSource, SourceEvidence, Memory.canonical_text)
        .join(MediaSource, MediaSource.id == MemorySourceLink.source_id)
        .join(SourceEvidence, SourceEvidence.id == MemorySourceLink.evidence_id)
        .join(Memory, Memory.id == MemorySourceLink.memory_id)
        .where(MemorySourceLink.legacy_id == legacy.id, MediaSource.legacy_id == legacy.id,
               SourceEvidence.legacy_id == legacy.id, Memory.legacy_id == legacy.id,
               MemorySourceLink.memory_id.in_(memory_ids)).order_by(MemorySourceLink.approved_at, MemorySourceLink.id)).all()
    seen = set()
    for link, source, evidence, canonical_text in rows:
        key = (link.memory_id, source.id)
        if key in seen or len(result[link.memory_id]) >= 8:
            continue
        seen.add(key)
        permitted = owner or source.uploader_user_id == user_id
        removed = source.state in {"deleting", "deleted"} or link.removed_at is not None
        state = "unavailable" if removed else ("stale" if hashlib.sha256(canonical_text.encode()).hexdigest() != link.approved_text_sha256 else link.support_state)
        result[link.memory_id].append({"source_id": source.id if permitted and not removed else None,
            "filename": source.original_filename if permitted and not removed else None,
            "locator": evidence.locator_json if permitted and not removed else None,
            "state": state, "can_open": permitted and not removed})
    return result
