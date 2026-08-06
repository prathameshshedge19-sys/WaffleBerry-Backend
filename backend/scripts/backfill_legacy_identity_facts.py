"""Idempotently project identity facts from approved structured memories."""

import argparse
from dataclasses import dataclass
from typing import Callable

from app.db import SessionLocal
from app.models.memory import Memory, MemoryReviewStatus
from app.services.memory.identity_facts import IdentityFactProjectionService


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--after-memory-id", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


@dataclass(frozen=True)
class BackfillResult:
    scanned: int
    created: int
    skipped: int
    failed: int
    scan_cursor: int
    resume_after_memory_id: int


def run_backfill(
    db,
    *,
    batch_size: int,
    after_memory_id: int = 0,
    dry_run: bool = False,
    projector=None,
    emit: Callable[[str], None] = print,
) -> BackfillResult:
    """Project approved memories with one durable transaction per memory."""
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    projector = projector or IdentityFactProjectionService()
    scan_cursor = after_memory_id
    resume_cursor = after_memory_id
    failure_seen = False
    scanned = created = skipped = failed = 0
    while True:
        rows = db.query(Memory).filter(
            Memory.memory_id > scan_cursor,
            Memory.review_status == MemoryReviewStatus.APPROVED,
            Memory.superseded_by_memory_id.is_(None),
        ).order_by(Memory.memory_id).limit(batch_size).all()
        if not rows:
            break
        for memory in rows:
            memory_id = memory.memory_id
            scanned += 1
            try:
                savepoint = db.begin_nested()
                projected = projector.project_memory(db, memory)
                if dry_run:
                    savepoint.rollback()
                else:
                    savepoint.commit()
                    db.commit()
            except Exception:
                db.rollback()
                failed += 1
                failure_seen = True
            else:
                created += projected
                skipped += int(projected == 0)
                if not failure_seen:
                    resume_cursor = memory_id
            scan_cursor = memory_id
        emit(
            f"scan_cursor={scan_cursor} "
            f"resume_after_memory_id={resume_cursor} scanned={scanned} "
            f"created={created} skipped={skipped} failed={failed}"
        )
    return BackfillResult(
        scanned=scanned,
        created=created,
        skipped=skipped,
        failed=failed,
        scan_cursor=scan_cursor,
        resume_after_memory_id=resume_cursor,
    )


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        raise SystemExit("--batch-size must be between 1 and 1000")
    db = SessionLocal()
    try:
        result = run_backfill(
            db,
            batch_size=args.batch_size,
            after_memory_id=args.after_memory_id,
            dry_run=args.dry_run,
        )
    finally:
        db.close()
    print(
        f"complete scanned={result.scanned} created={result.created} "
        f"skipped={result.skipped} failed={result.failed} "
        f"resume_after_memory_id={result.resume_after_memory_id} "
        f"dry_run={args.dry_run}"
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
