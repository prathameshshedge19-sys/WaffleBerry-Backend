"""Restricted local-backup expiry and fail-closed restore eligibility.

Policy: at most 30 days from capture, hourly enforcement with a one-day safety
margin. No blanket directory removal, symlink following, mtime refresh, or
best-effort success. A protected SQLite catalog lives OUTSIDE the backup root.
All regular files under the designated backup root are in scope (including
private env copies and QA material). Off-host snapshots need their own verified
expiry adapter; this module never claims to govern an unlisted backup system.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

MAX_AGE = 30 * 86400
EXPIRY_AGE = MAX_AGE - 86400
HEALTH_AGE = 2 * 3600
POLICY_VERSION = "local-backups-30d-v1"


class PolicyError(RuntimeError):
    pass


def private_path(path, *, directory=True):
    path = Path(path)
    if not path.is_absolute() or path == Path(path.anchor):
        raise PolicyError("policy_path_invalid")
    if path != path.resolve():
        raise PolicyError("policy_path_alias")
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PolicyError("policy_path_alias")
    info = path.stat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise PolicyError("policy_directory_required")
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise PolicyError("policy_regular_private_file_required")
    if os.name != "nt" and info.st_mode & 0o007:
        raise PolicyError("policy_path_public")
    return path


@contextmanager
def catalog(root, state, *, readonly=False):
    root, state = private_path(root), private_path(state)
    if root == state or root in state.parents or state in root.parents:
        raise PolicyError("policy_catalog_not_independent")
    target = state / "catalog.sqlite3"
    if target.exists():
        private_path(target, directory=False)
    old_mask = os.umask(0o077)
    try:
        db = sqlite3.connect(":memory:" if readonly else target, timeout=5)
        if readonly and target.exists():
            with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as source:
                source.backup(db)
    finally:
        os.umask(old_mask)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, digest TEXT NOT NULL, captured REAL NOT NULL, expired INTEGER NOT NULL DEFAULT 0)")
        db.execute("BEGIN IMMEDIATE")
        binding = db.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if binding and binding[0] != str(root):
            raise PolicyError("policy_root_changed")
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('root', ?)", (str(root),))
        yield root, db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def inventory(root):
    result = []
    root_device = root.stat().st_dev
    def unreadable(_):
        raise PolicyError("policy_inventory_incomplete")
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in dirs + files:
            child = Path(directory) / name
            info = child.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or child.resolve().is_relative_to(root) is not True
                    or info.st_dev != root_device or child.is_mount()):
                raise PolicyError("policy_unsafe_entry")
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise PolicyError("policy_hardlink_rejected")
                result.append(child)
    return result


def fingerprint(path):
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    handle = os.open(path, flags)
    try:
        current = os.fstat(handle)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)):
            raise PolicyError("policy_entry_changed")
        digest = hashlib.sha256()
        while chunk := os.read(handle, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(handle)
        if (current.st_size, current.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PolicyError("policy_entry_changed")
        return digest.hexdigest(), after
    finally:
        os.close(handle)


def captured_time(path, info, timestamp):
    # Existing named release backups preserve capture age even if copied later.
    match = re.search(r"(20\d{6})T(\d{6})Z", str(path))
    named = timestamp
    if match:
        from datetime import datetime, timezone
        named = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    return min(timestamp, info.st_mtime, named)


def expire(root, state, *, apply=False, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    removed = due = total = 0
    with catalog(root, state, readonly=not apply) as (root, db):
        # Validate the ENTIRE tree before deleting any file. Root is restricted
        # to the policy administrator; backup creators must use atomic rename.
        if apply:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_success', '0')")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
        paths = inventory(root)
        prepared = []
        for path in paths:
            total += 1
            key = str(path.relative_to(root))
            digest, info = fingerprint(path)
            existing = db.execute("SELECT digest,captured,expired FROM files WHERE path=?", (key,)).fetchone()
            if existing and existing[0] != digest:
                raise PolicyError("policy_backup_mutated")
            captured = captured_time(path, info, timestamp)
            duplicate = db.execute("SELECT MIN(captured) FROM files WHERE digest=?", (digest,)).fetchone()[0]
            if duplicate is not None:
                captured = min(captured, duplicate)
            if existing:
                captured = min(captured, existing[1])
            if apply:
                db.execute("INSERT INTO files(path,digest,captured,expired) VALUES(?,?,?,0) ON CONFLICT(path) DO UPDATE SET captured=MIN(captured,excluded.captured)", (key, digest, captured))
            if timestamp - captured >= EXPIRY_AGE or existing and existing[2]:
                due += 1
                if apply:
                    db.execute("UPDATE files SET expired=1 WHERE path=?", (key,))
                    prepared.append((path, info))
        if apply:
            # Capture dates and deletion intents survive a crash during unlink.
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            for path, info in prepared:
                # Exact regular file only; verify identity immediately before
                # unlink. Catalog tombstones survive copying/touching files.
                again = path.lstat()
                if (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                    raise PolicyError("policy_entry_changed")
                path.unlink()
                if path.exists():
                    raise PolicyError("policy_expiry_unconfirmed")
                removed += 1
        if apply:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_success', ?)", (str(timestamp),))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('policy', ?)", (POLICY_VERSION,))
    return {"policy": POLICY_VERSION, "files": total, "due": due, "removed": removed, "applied": apply}


def restore_eligible(root, state, backup, *, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    with catalog(root, state, readonly=True) as (root, db):
        backup = private_path(backup, directory=False)
        if not backup.is_relative_to(root):
            raise PolicyError("restore_outside_catalog")
        health = dict(db.execute("SELECT key,value FROM metadata"))
        if (health.get("policy") != POLICY_VERSION or
                not 0 <= timestamp - float(health.get("last_success", 0)) <= HEALTH_AGE):
            raise PolicyError("backup_expiry_unhealthy")
        record = db.execute("SELECT digest,captured,expired FROM files WHERE path=?", (str(backup.relative_to(root)),)).fetchone()
        if not record or record[2] or not 0 <= timestamp - record[1] < EXPIRY_AGE:
            raise PolicyError("restore_backup_expired_or_unknown")
        if fingerprint(backup)[0] != record[0]:
            raise PolicyError("restore_backup_changed")
    return True


def main():
    parser = argparse.ArgumentParser(description="30-day restricted backup expiry; dry-run by default")
    parser.add_argument("--root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-root")
    parser.add_argument("--restore-backup")
    args = parser.parse_args()
    if args.apply and args.confirm_root != args.root:
        parser.error("--apply requires the identical absolute --confirm-root")
    try:
        if args.restore_backup:
            restore_eligible(args.root, args.state, args.restore_backup)
            print(json.dumps({"backup_eligible": True, "traffic_release": False,
                "remaining_gate": "independent_deletion_journal_replay"}))
        else:
            print(json.dumps(expire(args.root, args.state, apply=args.apply)))
    except (PolicyError, OSError, sqlite3.Error):
        print(json.dumps({"event": "backup_policy_failed", "healthy": False}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
