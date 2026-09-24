"""Model-free, flag-independent account erasure worker and verified-support CLI."""
import argparse
import json
import time
import sqlite3
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.models.account_deletion import AccountDeletion
from app.services.account_deletion import finalize_account, now, request_account_deletion
from app.services.legacy_deletion import finalize_one
from app.services.media_storage import get_source_storage
from app.services.media_worker import MediaWorker
from app.services.visual_storage import VisualStorage
from app.services.visual_worker import VisualWorker
from app.services.voice_worker import VoiceWorker


class AccountDeletionWorker:
    def __init__(self, sessions=SessionLocal, storage=None):
        self.sessions = sessions
        self.storage = storage or get_source_storage()
        self.media = MediaWorker(sessions, self.storage, purge_only=True)
        self.boundary = VisualStorage(self.storage, sessions=sessions)
        self.visual = VisualWorker(sessions, self.boundary, provider=None)
        self.voice = VoiceWorker(sessions, self.storage, purge_only=True)

    def run_once(self):
        from app.services.deletion_journal import replay
        try:
            replay(self.sessions)
        except (OSError, sqlite3.Error):
            # External journal outages must not stop already durable SQL purges.
            # New admissions fail closed; health/restore checks also fail.
            print(json.dumps({"event": "deletion_journal_unavailable"}), flush=True)
        if self.boundary.writes is not None:
            self.boundary.writes.sweep()
        # These adapters never load models or enable creation/use flags.
        for worker in (self.media, self.visual, self.voice):
            try:
                worker.run_once()
            except Exception:
                # Registrations and leases remain durable. Never log private
                # provider exception strings, URLs, object keys or content.
                pass
        try:
            finalize_one(self.sessions, self.storage)
        except Exception:
            pass
        with self.sessions() as db:
            ids = list(db.scalars(select(AccountDeletion.id).where(
                AccountDeletion.state != "completed",
                or_(AccountDeletion.next_attempt_at.is_(None), AccountDeletion.next_attempt_at <= now()))
                .order_by(AccountDeletion.requested_at, AccountDeletion.id).limit(16)))
        outcome = "idle"
        for deletion_id in ids:
            try:
                outcome = finalize_account(self.sessions, self.storage, deletion_id)
                code = None if outcome == "completed" else "purge_pending"
            except Exception:
                outcome, code = "retry_wait", "cleanup_retryable"
            with self.sessions.begin() as db:
                row = db.scalar(select(AccountDeletion).where(AccountDeletion.id == deletion_id).with_for_update())
                if row is not None and row.state != "completed":
                    row.state = "waiting_for_purge"
                    row.attempts += 1
                    row.safe_error_code = code
                    row.next_attempt_at = now() + timedelta(seconds=min(300, 5 * 2 ** min(row.attempts, 6)))
        return outcome

    def close(self):
        self.voice.close()


def health_summary(sessions=SessionLocal):
    timestamp = now()
    with sessions() as db:
        count, oldest = db.execute(select(func.count(), func.min(AccountDeletion.requested_at)).where(
            AccountDeletion.state != "completed")).one()
    if oldest is not None and oldest.tzinfo is None:
        from datetime import timezone
        oldest = oldest.replace(tzinfo=timezone.utc)
    age = max(0, (timestamp - oldest).total_seconds()) if oldest else 0
    journal_healthy = True
    try:
        from app.services.deletion_journal import configured, journal
        settings = configured()
        if settings is not None:
            with journal(settings):
                pass
    except Exception:
        journal_healthy = False
    return {"event": "account_deletion_health", "pending": count,
            "journal_healthy": journal_healthy,
            "oldest_pending_seconds": int(age), "warning": age >= 3600 or not journal_healthy,
            "critical": age >= 86400}


def main():
    parser = argparse.ArgumentParser(description="Durable account deletion; no production use without reviewed activation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    sub.add_parser("initialize-journal")
    sub.add_parser("restore-gate")
    sub.add_parser("health")
    support = sub.add_parser("verified-support-request")
    support.add_argument("--user-id", type=int, required=True)
    support.add_argument("--confirm-user-id", type=int, required=True)
    support.add_argument("--ownership-verified", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "health":
        result = health_summary()
        print(json.dumps(result), flush=True)
        raise SystemExit(1 if result["warning"] else 0)
    if args.command in {"initialize-journal", "restore-gate"}:
        from app.services.deletion_journal import initialize, restore_gate
        if args.command == "initialize-journal":
            initialize(SessionLocal)
        else:
            restore_gate(SessionLocal)
        print(json.dumps({"event": args.command, "success": True}), flush=True)
        return
    if args.command == "verified-support-request":
        if args.user_id <= 0 or args.confirm_user_id != args.user_id or not args.ownership_verified:
            parser.error("Exact verified account confirmation required")
        with SessionLocal.begin() as db:
            request_account_deletion(db, args.user_id, verified_support=True)
        print(json.dumps({"event": "account_deletion_requested", "status": "deleting"}), flush=True)
        return
    worker = AccountDeletionWorker()
    try:
        while True:
            outcome = worker.run_once()
            print(json.dumps({"event": "account_deletion_cycle", "outcome": outcome}), flush=True)
            if args.once:
                break
            time.sleep(5)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
