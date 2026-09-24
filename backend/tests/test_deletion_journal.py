from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.account_deletion import AccountDeletion, DeletionLineage
from app.models.user import User
from app.services.account_deletion import finalize_account, request_account_deletion
from app.services.backup_retention import PolicyError
from app.services.deletion_journal import initialize, replay, restore_gate
from app.services.legacy_deletion import finalize_one
from tests.test_media_sources_l16 import media_db


@pytest.fixture
def independent_journal(media_db, tmp_path, monkeypatch):
    sessions, storage, _ = media_db
    root = tmp_path / "independent-volume"; root.mkdir(mode=0o700)
    monkeypatch.setenv("DELETION_JOURNAL_PATH", str(root))
    monkeypatch.setenv("DELETION_LINEAGE", str(uuid4()))
    monkeypatch.setenv("VOICE_TEMP_PATH", str(tmp_path / "voice"))
    get_settings.cache_clear()
    initialize(sessions)
    yield sessions, storage
    get_settings.cache_clear()


def test_verified_intent_survives_db_rollback_and_preserves_other_account(independent_journal):
    sessions, storage = independent_journal
    with pytest.raises(RuntimeError):
        with sessions.begin() as db:
            request_id = request_account_deletion(db, 1, verified_support=True).id
            raise RuntimeError("simulated commit failure")
    with sessions() as db:
        assert db.get(User, 1).deletion_requested_at is None
    with pytest.raises(PolicyError, match="pending"):
        restore_gate(sessions)
    assert replay(sessions) == 1
    with sessions() as db:
        assert db.scalar(select(AccountDeletion.id)) == request_id
    assert finalize_one(sessions, storage) == "legacy_erased"
    assert finalize_account(sessions, storage, request_id) == "completed"
    assert restore_gate(sessions)
    assert replay(sessions) == 0
    with sessions() as db:
        assert db.get(User, 1) is None and db.get(User, 2) is not None


def test_lineage_mismatch_and_missing_receipt_fail_closed(independent_journal):
    sessions, _ = independent_journal
    with sessions.begin() as db:
        request_account_deletion(db, 1, verified_support=True)
        db.get(DeletionLineage, 1).lineage = str(uuid4())
    with pytest.raises(PolicyError, match="lineage"):
        replay(sessions)
    with pytest.raises(PolicyError, match="lineage"):
        restore_gate(sessions)


def test_production_cannot_silently_omit_journal():
    from app.services.deletion_journal import configured
    with pytest.raises(PolicyError, match="required"):
        configured(SimpleNamespace(legarya_debug=False, deletion_journal_path=None, deletion_lineage=None))
