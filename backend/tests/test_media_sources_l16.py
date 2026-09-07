"""L16 Phase B source-library foundation acceptance tests."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, build_engine
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.legacy import Legacy
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource, SourceState
from app.models.memory import Memory, MemoryStatus
from app.models.user import User
from app.services.media_sources import MediaSourceService, sanitize_filename, validate_source_bytes
from app.services.media_storage import LocalSourceStorage
from app.services.media_worker import MediaWorker


@pytest.fixture
def media_db(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("MEDIA_ENABLED", "true")
    monkeypatch.setenv("MEDIA_LOCAL_STORAGE_PATH", str(tmp_path / "objects"))
    settings = get_settings()
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = LocalSourceStorage(str(tmp_path / "objects"))
    with factory.begin() as db:
        db.add_all([
            User(id=1, full_name="Owner", email="owner@example.com", password_hash="x", is_verified=True),
            User(id=2, full_name="Collaborator", email="collab@example.com", password_hash="x", is_verified=True),
            User(id=3, full_name="Other", email="other@example.com", password_hash="x", is_verified=True),
            Legacy(id=1, owner_user_id=1, subject_name="Asha", setup_status="active"),
            Legacy(id=2, owner_user_id=3, subject_name="Other Legacy", setup_status="active"),
            LegacyCollaborator(id=1, legacy_id=1, user_id=2, role="collaborator", status=CollaboratorStatus.ACTIVE.value),
        ])
    try:
        yield factory, storage, settings
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _reserve(factory, storage, user_id=1, legacy_id=1, *, kind="document", mime="text/plain", size=5, filename="../note.txt"):
    with factory() as db:
        user = db.get(User, user_id)
        source = MediaSourceService(storage=storage).create(db, user, legacy_id, kind=kind, mime_type=mime, filename=filename, size_bytes=size, upload_request_key=str(uuid4()))
        return source.id


def test_owner_upload_is_scoped_and_creates_no_memory(media_db):
    factory, storage, _ = media_db
    source_id = _reserve(factory, storage)
    with factory() as db:
        source = db.get(MediaSource, source_id)
        assert source.legacy_id == 1 and source.state == SourceState.UPLOADING.value
        assert db.scalar(select(func.count()).select_from(Memory)) == 0
        assert db.scalar(select(func.count()).select_from(MediaArtifact)) == 1
        assert db.scalar(select(func.count()).select_from(MediaProcessingJob)) == 1
        assert source.original_filename == "note.txt"


def test_collaborator_can_submit_but_cannot_read_other_source_or_delete(media_db):
    factory, storage, _ = media_db
    owner_source = _reserve(factory, storage, user_id=1)
    with factory() as db:
        service = MediaSourceService(storage=storage); collaborator = db.get(User, 2)
        own = service.create(db, collaborator, 1, kind="document", mime_type="text/plain", filename="own.txt", size_bytes=4, upload_request_key=str(uuid4()))
        assert [item.id for item in service.list(db, collaborator, 1)] == [own.id]
        with pytest.raises(Exception): service.get(db, collaborator, 1, owner_source)
        with pytest.raises(Exception): service.delete(db, collaborator, 1, own.id)


def test_foreign_legacy_id_cannot_use_guessed_source(media_db):
    factory, storage, _ = media_db
    source_id = _reserve(factory, storage)
    with factory() as db:
        with pytest.raises(Exception): MediaSourceService(storage=storage).get(db, db.get(User, 1), 2, source_id)


@pytest.mark.parametrize("kind,mime,data", [
    ("image", "image/png", b"\x89PNG\r\n\x1a\nrest"),
    ("document", "application/pdf", b"%PDF-1.7 rest"),
    ("audio", "audio/mpeg", b"ID3rest"),
    ("video", "video/mp4", b"\x00\x00\x00\x18ftypisomrest"),
])
def test_supported_signatures_are_accepted(kind, mime, data):
    assert validate_source_bytes(kind, mime, data)


def test_spoofed_type_oversize_and_unsafe_filename_are_rejected(media_db):
    _, _, settings = media_db
    with pytest.raises(Exception): validate_source_bytes("document", "application/pdf", b"not a pdf", settings)
    with pytest.raises(Exception): validate_source_bytes("image", "image/png", b"\x89PNG\r\n\x1a\n" + b"x" * (settings.media_max_photo_bytes + 1), settings)
    assert sanitize_filename("C:\\secret\\..\\diary.txt") == "diary.txt"


def test_receive_is_idempotent_and_never_writes_canonical_memory(media_db):
    factory, storage, _ = media_db
    source_id = _reserve(factory, storage, size=5)
    with factory() as db:
        service = MediaSourceService(storage=storage); user = db.get(User, 1)
        source = service.receive(db, user, 1, source_id, b"hello")
        again = service.receive(db, user, 1, source_id, b"hello")
        assert source.sha256 == again.sha256 and source.state == SourceState.QUEUED.value
        assert db.scalar(select(func.count()).select_from(Memory)) == 0
        assert storage.exists(db.scalar(select(MediaArtifact.object_key).where(MediaArtifact.source_id == source_id)))


def test_delete_fences_job_and_worker_purges_without_deleting_memory(media_db):
    factory, storage, _ = media_db
    source_id = _reserve(factory, storage, size=5)
    with factory() as db:
        service = MediaSourceService(storage=storage); user = db.get(User, 1)
        source = service.receive(db, user, 1, source_id, b"hello")
        db.add(Memory(legacy_id=1, canonical_text="Asha likes tea", category="preference", subject_reference="Asha", source_language="english", source_excerpt="", confidence=1, status=MemoryStatus.ACTIVE.value, operation_type="new", explicit_save=True, normalized_fingerprint="a" * 64))
        db.commit()
        deleting = service.delete(db, user, 1, source_id)
        assert deleting.state == SourceState.DELETING.value
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.legacy_id == 1, Memory.status == MemoryStatus.ACTIVE.value)) == 1
    assert MediaWorker(sessions=factory, storage=storage).run_once() == "purged"
    with factory() as db:
        assert db.get(MediaSource, source_id).state == SourceState.DELETED.value
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.legacy_id == 1, Memory.status == MemoryStatus.ACTIVE.value)) == 1
        assert not storage.exists(db.scalar(select(MediaArtifact.object_key).where(MediaArtifact.source_id == source_id)))


def test_phase_b_worker_extract_is_explicitly_deferred_and_canonical_safe(media_db):
    factory, storage, _ = media_db
    source_id = _reserve(factory, storage, size=5)
    with factory() as db:
        MediaSourceService(storage=storage).receive(db, db.get(User, 1), 1, source_id, b"hello")
    assert MediaWorker(sessions=factory, storage=storage).run_once() == "deferred"
    with factory() as db:
        source = db.get(MediaSource, source_id)
        assert source.state == SourceState.FAILED.value and source.last_error_code == "extraction_deferred_phase_c"
        assert db.scalar(select(func.count()).select_from(Memory)) == 0
