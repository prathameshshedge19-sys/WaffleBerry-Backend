import os
from pathlib import Path

import pytest

from app.services.backup_retention import (
    EXPIRY_AGE, HEALTH_AGE, PolicyError, expire, restore_eligible,
)


@pytest.fixture
def roots(tmp_path):
    root, state = tmp_path / "backups", tmp_path / "policy"
    root.mkdir(mode=0o700); state.mkdir(mode=0o700)
    return root, state


def test_dry_run_no_writes_exact_expiry_and_restore_gate(roots):
    root, state = roots
    old, new = root / "old.dump", root / "new.dump"
    old.write_bytes(b"old synthetic backup"); new.write_bytes(b"new synthetic backup")
    now = new.stat().st_mtime
    os.utime(old, (now - EXPIRY_AGE, now - EXPIRY_AGE))
    assert expire(root, state, timestamp=now)["due"] == 1
    assert old.exists() and list(state.iterdir()) == []
    result = expire(root, state, apply=True, timestamp=now)
    assert result["removed"] == 1 and not old.exists() and new.exists()
    assert restore_eligible(root, state, new, timestamp=now)
    with pytest.raises(PolicyError, match="unhealthy"):
        restore_eligible(root, state, new, timestamp=now + HEALTH_AGE + 1)


def test_touch_copy_and_restore_cannot_extend_retention(roots):
    root, state = roots
    first = root / "first.dump"
    first.write_bytes(b"same backup")
    now = first.stat().st_mtime
    expire(root, state, apply=True, timestamp=now)
    later = now + EXPIRY_AGE
    os.utime(first, (later, later))
    copied = root / "copy.dump"
    copied.write_bytes(first.read_bytes()); os.utime(copied, (later, later))
    assert expire(root, state, apply=True, timestamp=later)["removed"] == 2
    first.write_bytes(b"same backup"); os.utime(first, (later, later))
    assert expire(root, state, apply=True, timestamp=later)["removed"] == 1


def test_mutated_unknown_and_outside_backups_cannot_be_restored(roots):
    root, state = roots
    backup = root / "one.dump"
    backup.write_bytes(b"one")
    now = backup.stat().st_mtime
    expire(root, state, apply=True, timestamp=now)
    unknown = root / "unknown.dump"; unknown.write_bytes(b"two")
    with pytest.raises(PolicyError, match="unknown"):
        restore_eligible(root, state, unknown, timestamp=now)
    backup.write_bytes(b"mutated")
    with pytest.raises(PolicyError, match="changed"):
        restore_eligible(root, state, backup, timestamp=now)
    with pytest.raises(PolicyError, match="mutated"):
        expire(root, state, apply=True, timestamp=now)
    assert backup.exists()


def test_hardlinks_and_nested_catalog_fail_closed(roots):
    root, state = roots
    source = root / "first.dump"; source.write_bytes(b"synthetic")
    os.link(source, root / "alias.dump")
    with pytest.raises(PolicyError, match="hardlink"):
        expire(root, state, apply=True)
    nested = root / "catalog"; nested.mkdir(mode=0o700)
    with pytest.raises(PolicyError, match="independent"):
        expire(root, nested, apply=True)
    assert source.exists()


def test_failed_unlink_is_not_health_success_and_intent_survives(roots, monkeypatch):
    root, state = roots
    target = root / "old.dump"; target.write_bytes(b"synthetic")
    now = target.stat().st_mtime
    expire(root, state, apply=True, timestamp=now)
    original = Path.unlink
    def failed(path, *a, **kw):
        if path == target:
            raise PermissionError()
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "unlink", failed)
    with pytest.raises(PermissionError):
        expire(root, state, apply=True, timestamp=now + EXPIRY_AGE)
    with pytest.raises(PolicyError, match="unhealthy"):
        restore_eligible(root, state, target, timestamp=now + EXPIRY_AGE)
    monkeypatch.setattr(Path, "unlink", original)
    os.utime(target, (now + EXPIRY_AGE, now + EXPIRY_AGE))
    assert expire(root, state, apply=True, timestamp=now + EXPIRY_AGE)["removed"] == 1


def test_unreadable_subdirectory_invalidates_health_not_silent_success(roots, monkeypatch):
    root, state = roots
    target = root / "current.dump"; target.write_bytes(b"synthetic")
    now = target.stat().st_mtime
    expire(root, state, apply=True, timestamp=now)
    def failed_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError())
        return iter(())
    monkeypatch.setattr(os, "walk", failed_walk)
    with pytest.raises(PolicyError, match="incomplete"):
        expire(root, state, apply=True, timestamp=now)
    with pytest.raises(PolicyError, match="unhealthy"):
        restore_eligible(root, state, target, timestamp=now)
    assert target.exists()
