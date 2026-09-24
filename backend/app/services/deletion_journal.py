"""Write-ahead erasure obligations on a volume independent of DB restores.

Only a verified, confirmed deletion may append. Disk acknowledgement precedes
the SQL changes: after a crash/rollback the accepted intention is replayable.
No email, tokens, content or storage credentials enter this journal. Mounting
the current independent volume is an operational requirement, not something
an application can infer from the path name.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config import get_settings
from app.models.account_deletion import AccountDeletion, DeletionLineage
from app.models.user import User
from app.services.backup_retention import private_path, PolicyError


def configured(settings=None):
    settings = settings or get_settings()
    if settings.legarya_debug and not settings.deletion_journal_path:
        return None
    if not settings.deletion_journal_path or not settings.deletion_lineage:
        raise PolicyError("independent_deletion_journal_required")
    UUID(settings.deletion_lineage)
    if not settings.legarya_debug and not getattr(settings, "deletion_journal_volume_uuid", None):
        raise PolicyError("independent_journal_volume_required")
    return settings


def verify_volume(root, volume_uuid):
    """Do not silently fall back to the root disk or an unrelated mounted disk."""
    try:
        canonical = str(UUID(volume_uuid))
        device = (Path("/dev/disk/by-uuid") / canonical).stat()
        if not stat.S_ISBLK(device.st_mode) or not os.path.ismount(root) or root.stat().st_dev != device.st_rdev:
            raise PolicyError("independent_journal_volume_mismatch")
    except (OSError, ValueError, TypeError, AttributeError):
        raise PolicyError("independent_journal_volume_unavailable") from None


@contextmanager
def journal(settings, *, initialize=False):
    root = private_path(settings.deletion_journal_path)
    volume_uuid = getattr(settings, "deletion_journal_volume_uuid", None)
    if volume_uuid:
        verify_volume(root, volume_uuid)
    elif not settings.legarya_debug:
        raise PolicyError("independent_journal_volume_required")
    target = root / "obligations.sqlite3"
    if not initialize and not target.is_file():
        raise PolicyError("deletion_journal_not_initialized")
    if target.exists():
        private_path(target, directory=False)
    mask = os.umask(0o077)
    try:
        connection = sqlite3.connect(target, timeout=5)
    finally:
        os.umask(mask)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        if initialize:
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), lineage TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS obligations (user_id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, requested REAL NOT NULL, completed REAL)")
            connection.execute("INSERT OR IGNORE INTO metadata VALUES (1, ?)", (settings.deletion_lineage,))
        row = connection.execute("SELECT lineage FROM metadata WHERE id=1").fetchone()
        if row != (settings.deletion_lineage,):
            raise PolicyError("deletion_journal_lineage_mismatch")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(sessions, settings=None):
    """Explicit operator operation, never silently performed by request/restore."""
    settings = configured(settings)
    if settings is None:
        raise PolicyError("deletion_journal_configuration_required")
    with sessions.begin() as db:
        row = db.get(DeletionLineage, 1)
        if row is None:
            db.add(DeletionLineage(id=1, lineage=settings.deletion_lineage))
        elif row.lineage != settings.deletion_lineage:
            raise PolicyError("database_lineage_mismatch")
        with journal(settings, initialize=True) as external:
            for receipt in db.scalars(select(AccountDeletion).order_by(AccountDeletion.target_user_id)):
                external.execute("INSERT OR IGNORE INTO obligations(user_id,request_id,requested) VALUES(?,?,?)",
                    (receipt.target_user_id, receipt.id, receipt.requested_at.timestamp()))
                stored = external.execute("SELECT request_id FROM obligations WHERE user_id=?", (receipt.target_user_id,)).fetchone()
                if stored != (receipt.id,):
                    raise PolicyError("deletion_receipt_mismatch")


def validate_lineage(db, settings):
    row = db.get(DeletionLineage, 1)
    if row is None or row.lineage != settings.deletion_lineage:
        raise PolicyError("database_lineage_mismatch")


def record_intent(db, user_id):
    settings = configured()
    if settings is None:
        return str(uuid4())
    validate_lineage(db, settings)
    with journal(settings) as external:
        external.execute("INSERT OR IGNORE INTO obligations(user_id,request_id,requested) VALUES(?,?,?)",
                         (user_id, str(uuid4()), time.time()))
        return external.execute("SELECT request_id FROM obligations WHERE user_id=?", (user_id,)).fetchone()[0]


def replay(sessions, settings=None, *, limit=32):
    settings = configured(settings)
    if settings is None:
        return 0
    with sessions() as db:
        validate_lineage(db, settings)
    with journal(settings) as external:
        entries = external.execute("SELECT user_id,request_id FROM obligations ORDER BY user_id").fetchall()
    from app.services.account_deletion import request_account_deletion
    count = 0
    for user_id, request_id in entries:
        with sessions.begin() as db:
            user = db.get(User, user_id)
            receipt = db.scalar(select(AccountDeletion).where(AccountDeletion.target_user_id == user_id))
            if user is not None and receipt is None:
                request_account_deletion(db, user_id, verified_support=True)
                count += 1
            elif receipt is not None and receipt.id != request_id:
                raise PolicyError("deletion_receipt_mismatch")
        if count >= limit:
            break
    return count


def restore_gate(sessions, settings=None):
    """Fail until EVERY independent obligation is absent and positively complete.

    This deliberately checks completed external entries too: restoring an older
    product DB cannot make them irrelevant. Run only with traffic/writers off.
    """
    settings = configured(settings)
    if settings is None:
        raise PolicyError("restore_requires_independent_journal")
    with journal(settings) as external:
        entries = external.execute("SELECT user_id,request_id FROM obligations").fetchall()
    with sessions() as db:
        validate_lineage(db, settings)
        for user_id, request_id in entries:
            if db.get(User, user_id) is not None:
                raise PolicyError("restore_erasure_pending")
            receipt = db.get(AccountDeletion, request_id)
            if receipt is None or receipt.target_user_id != user_id or receipt.state != "completed":
                raise PolicyError("restore_erasure_unproven")
    return True
