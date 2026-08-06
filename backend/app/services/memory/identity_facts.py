"""Approved-memory projection into stable Legacy identity facts."""

import unicodedata

from sqlalchemy.orm import Session

from app.models.memory import (
    IdentityFactStatus,
    IdentityFactType,
    LegacyIdentityFact,
    Memory,
    MemoryReviewStatus,
)


_SINGLETON_TYPES = {
    IdentityFactType.FULL_NAME,
    IdentityFactType.BIRTH_DATE,
    IdentityFactType.BIRTHPLACE,
    IdentityFactType.HOMETOWN,
}


def normalize_identity_value(value: str) -> str:
    """Build a Unicode-safe comparison key without changing canonical text."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class IdentityFactProjectionService:
    """Idempotently project explicit claims from approved source memories."""

    def project_memory(self, db: Session, memory: Memory) -> int:
        if (
            memory.review_status != MemoryReviewStatus.APPROVED
            or memory.superseded_by_memory_id is not None
        ):
            return self.remove_for_memory(db, memory.memory_id)
        provenance = next(
            (
                item for item in memory.provenance
                if str(getattr(item.speaker, "value", item.speaker)).casefold() == "user"
                and item.excerpt
            ),
            None,
        )
        if provenance is None:
            return 0
        details = memory.details or {}
        claims = details.get("identity_facts", []) if isinstance(details, dict) else []
        created = 0
        for raw in claims:
            if not isinstance(raw, dict):
                continue
            try:
                fact_type = IdentityFactType(raw.get("fact_type"))
            except (TypeError, ValueError):
                continue
            value = raw.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            normalized = normalize_identity_value(value)
            relationship = raw.get("relationship") or ""
            existing = db.query(LegacyIdentityFact).filter(
                LegacyIdentityFact.legacy_id == memory.legacy_id,
                LegacyIdentityFact.fact_type == fact_type,
                LegacyIdentityFact.normalized_value == normalized,
                LegacyIdentityFact.relationship == relationship,
            ).first()
            if existing is not None:
                continue
            fact = LegacyIdentityFact(
                legacy_id=memory.legacy_id,
                fact_type=fact_type,
                value=value,
                normalized_value=normalized,
                relationship=relationship,
                confidence=raw.get("confidence", memory.extraction_confidence or 1),
                uncertainty_note=raw.get("uncertainty_note") or memory.uncertainty_note,
                source_memory_id=memory.memory_id,
                source_provenance_id=provenance.provenance_id,
                contradiction_group_id=memory.contradiction_group_id,
            )
            db.add(fact)
            db.flush()
            created += 1
            if fact_type in _SINGLETON_TYPES:
                conflicts = db.query(LegacyIdentityFact).filter(
                    LegacyIdentityFact.legacy_id == memory.legacy_id,
                    LegacyIdentityFact.fact_type == fact_type,
                    LegacyIdentityFact.identity_fact_id != fact.identity_fact_id,
                    LegacyIdentityFact.normalized_value != normalized,
                ).all()
                if conflicts:
                    fact.status = IdentityFactStatus.CONFLICTING
                    for conflict in conflicts:
                        conflict.status = IdentityFactStatus.CONFLICTING
        return created

    @staticmethod
    def remove_for_memory(db: Session, memory_id: int) -> int:
        return db.query(LegacyIdentityFact).filter(
            LegacyIdentityFact.source_memory_id == memory_id
        ).delete(synchronize_session=False)

    def backfill(self, db: Session, *, offset: int = 0, batch_size: int = 100, dry_run: bool = False):
        memories = db.query(Memory).filter(
            Memory.review_status == MemoryReviewStatus.APPROVED,
            Memory.superseded_by_memory_id.is_(None),
        ).order_by(Memory.memory_id).offset(offset).limit(batch_size).all()
        created = 0
        for memory in memories:
            created += self.project_memory(db, memory)
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return {"scanned": len(memories), "created": created, "next_offset": offset + len(memories)}
