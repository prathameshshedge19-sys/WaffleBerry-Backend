"""Explicit standalone durable worker; never started by a chat/visitor request.

Run `python -m app.services.personality_worker --once` after applying 0014.
No secrets, source texts, or provider exceptions are logged.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

from sqlalchemy import exists, or_, select, update

from app.database import SessionLocal
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.personality import LegacyPersonalityProfile as Row
from app.services.legacy_personality import BUILDER_ID, POLICY_VERSION, SCHEMA_VERSION, derive_profile, load_evidence, validate_profile
from app.services.personality_invalidation import invalidate_in_transaction


@dataclass(frozen=True)
class Claim:
    legacy_id: int
    generation: int
    token: str


class PersonalityWorker:
    def __init__(self, sessions=SessionLocal, *, builder=derive_profile, lease_seconds=120, clock=None):
        if lease_seconds < 1:
            raise ValueError("Lease must be positive")
        self.sessions = sessions
        self.builder = builder
        self.lease_seconds = lease_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def claim(self):
        now = self.clock()
        available = (
            Row.build_status.in_(("pending", "failed", "building")),
            or_(Row.lease_token.is_(None), Row.lease_expires_at <= now),
            or_(Row.next_attempt_at.is_(None), Row.next_attempt_at <= now),
        )
        with self.sessions.begin() as db:
            candidates = db.execute(select(Row.legacy_id, Row.source_generation).where(*available).order_by(Row.updated_at, Row.legacy_id).limit(16)).all()
            for legacy_id, generation in candidates:
                token = str(uuid4())
                result = db.execute(update(Row).where(Row.legacy_id == legacy_id, Row.source_generation == generation, *available).values(
                    lease_token=token, lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    build_status="building", attempts=Row.attempts + 1, updated_at=now,
                ))
                if result.rowcount == 1:
                    return Claim(legacy_id, generation, token)
        return None

    def build(self, claim):
        try:
            with self.sessions() as db:
                row = db.get(Row, claim.legacy_id)
                if row is None or row.lease_token != claim.token or row.source_generation != claim.generation:
                    self._release_stale(claim)
                    return "stale"
                evidence = load_evidence(db, claim.legacy_id)
            # No transaction/row lock remains open during derivation.
            output = self.builder(claim.legacy_id, claim.generation, evidence)
            profile = validate_profile(output, claim.legacy_id, claim.generation, evidence)
            now = self.clock()
            with self.sessions.begin() as db:
                result = db.execute(update(Row).where(
                    Row.legacy_id == claim.legacy_id, Row.source_generation == claim.generation,
                    Row.lease_token == claim.token, Row.lease_expires_at > now,
                ).values(
                    profile_json=profile.model_dump(mode="json"), built_generation=claim.generation,
                    schema_version=SCHEMA_VERSION, policy_version=POLICY_VERSION, builder_id=BUILDER_ID,
                    build_status="ready", built_at=now, updated_at=now, attempts=0,
                    lease_token=None, lease_expires_at=None, next_attempt_at=None, last_error_code=None,
                ))
                published = result.rowcount == 1
            if not published:
                self._release_stale(claim)
            return "ready" if published else "stale"
        except Exception:
            # Never expose stored personal text, credentials, or exception payloads.
            self._fail(claim)
            return "failed"

    def _release_stale(self, claim):
        with self.sessions.begin() as db:
            db.execute(update(Row).where(Row.legacy_id == claim.legacy_id, Row.lease_token == claim.token).values(
                build_status="pending", lease_token=None, lease_expires_at=None, updated_at=self.clock(),
            ))

    def _fail(self, claim):
        now = self.clock()
        with self.sessions.begin() as db:
            row = db.get(Row, claim.legacy_id)
            if row is None or row.lease_token != claim.token:
                return
            same_generation = row.source_generation == claim.generation
            attempts = row.attempts
            db.execute(update(Row).where(Row.legacy_id == claim.legacy_id, Row.lease_token == claim.token, Row.source_generation == row.source_generation).values(
                profile_json=None, build_status="failed" if same_generation else "pending",
                lease_token=None, lease_expires_at=None, updated_at=now,
                next_attempt_at=now + timedelta(seconds=min(300, 2 ** min(attempts, 8))) if same_generation else None,
                last_error_code="build_failed" if same_generation else None,
            ))

    def run_once(self):
        claim = self.claim()
        return self.build(claim) if claim else "idle"

    def enqueue_existing(self, limit=100):
        """Explicit bounded backfill, not a visitor-triggered lazy rebuild."""
        if not 1 <= limit <= 1000:
            raise ValueError("Backfill limit out of range")
        with self.sessions.begin() as db:
            ids = db.scalars(select(Legacy.id).where(
                ~exists(select(Row.legacy_id).where(Row.legacy_id == Legacy.id)),
                exists(select(Memory.id).where(Memory.legacy_id == Legacy.id, Memory.status == "active")),
            ).order_by(Legacy.id).limit(limit)).all()
            invalidate_in_transaction(db.connection(), ids)
            return len(ids)


def main():
    parser = argparse.ArgumentParser(description="Rebuild isolated L13 derived profiles")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--enqueue-existing", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds < 0.1:
        parser.error("--poll-seconds must be at least 0.1")
    worker = PersonalityWorker()
    if args.enqueue_existing:
        worker.enqueue_existing()
    while True:
        try:
            result = worker.run_once()
        except Exception:
            result = "worker_unavailable"
        print(result, flush=True)
        if args.once:
            return
        if result in ("idle", "failed", "worker_unavailable"):
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
