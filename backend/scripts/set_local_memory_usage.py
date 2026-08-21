"""Create or remove clearly marked local-only approved memories for quota testing."""

import argparse
from datetime import datetime, timezone

from app.config import get_settings
from app.crud.user import UserCRUD
from app.db import SessionLocal
from app.models.memory import Memory, MemoryProvenance, MemoryReviewStatus, MemoryType
from app.services.quota import QuotaService, get_plan_limits


PREFIX = "[LOCAL MEMORY QUOTA TEST]"


def main() -> None:
    if not get_settings().debug:
        raise SystemExit("This helper is available only when DEBUG=true.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--legacy-id", required=True, type=int)
    parser.add_argument("--used", type=int)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.cleanup == (args.used is not None):
        parser.error("Choose exactly one of --used or --cleanup.")
    if args.used is not None and args.used < 0:
        parser.error("--used cannot be negative.")

    with SessionLocal() as db:
        user = UserCRUD.get_user_by_email(db, args.email)
        if user is None:
            raise SystemExit("User not found.")
        QuotaService(db).check_memory_capacity(user, args.legacy_id)
        synthetic = db.query(Memory).filter(
            Memory.legacy_id == args.legacy_id,
            Memory.title.like(f"{PREFIX}%"),
        ).all()
        for memory in synthetic:
            db.delete(memory)
        db.flush()
        if args.cleanup:
            db.commit()
            print(f"Removed {len(synthetic)} synthetic memories.")
            return

        real_used = db.query(Memory.memory_id).filter(
            Memory.legacy_id == args.legacy_id,
            Memory.review_status == MemoryReviewStatus.APPROVED,
        ).count()
        limit = get_plan_limits(user.plan).memories_per_legacy
        if args.used > limit:
            parser.error(f"--used cannot exceed this user's limit ({limit}).")
        if real_used > args.used:
            raise SystemExit(
                f"This Legacy already has {real_used} real approved memories; "
                f"cannot reduce it to {args.used}."
            )
        now = datetime.now(timezone.utc)
        for index in range(args.used - real_used):
            marker = f"{PREFIX} {index + 1}"
            memory = Memory(
                legacy_id=args.legacy_id,
                memory_type=MemoryType.ATOMIC,
                category="other",
                title=marker,
                summary=f"Synthetic local capacity record {index + 1}.",
                normalized_fingerprint=(
                    f"local-memory-quota-{args.legacy_id}-{index + 1}"
                ),
                importance=1,
                extraction_confidence=1,
                review_status=MemoryReviewStatus.APPROVED,
                reviewed_at=now,
                reviewed_by_user_id=user.user_id,
            )
            memory.provenance.append(MemoryProvenance(
                source_type="local_debug",
                source_locator={"helper": "set_local_memory_usage"},
                excerpt=marker,
                speaker="debug_helper",
                extractor_version="phase-11.5-local",
            ))
            db.add(memory)
        db.commit()
        final_used = QuotaService(db).check_memory_capacity(
            user, args.legacy_id
        ).used
        print(
            f"Legacy {args.legacy_id} now has {final_used}/{limit} approved "
            "canonical memories. Synthetic rows are visibly marked."
        )


if __name__ == "__main__":
    main()
