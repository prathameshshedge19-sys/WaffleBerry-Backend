"""Opt-in live PostgreSQL acceptance for the L17 timeline control plane."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import build_engine
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity
from app.models.media_intelligence import SourceEvidence
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource
from app.models.timeline import LifeEvent, LifeEventEntity, LifeEventEvidence, LifeEventMemory
from app.models.user import User
from app.services.timeline import TimelineService, serialize_event


@pytest.fixture
def pg():
    url = os.environ.get("L17_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable L17 PostgreSQL database")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database in {"l17_test_phase_b", "l17_test_phase_b_final"}
    engine = build_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as db:
        assert db.execute(text("SELECT version()" )).scalar_one().startswith("PostgreSQL ")
        db.execute(text("TRUNCATE life_event_entities, life_event_evidence, life_event_memories, life_events, source_evidence, media_artifacts, media_processing_jobs, media_sources, memory_entity_links, memory_entities, memories, memory_revisions, legacies, users RESTART IDENTITY CASCADE"))
        db.execute(text("INSERT INTO users (id, full_name, email, password_hash, is_verified) VALUES (1, 'L17 Owner', 'l17-owner@example.com', 'x', true)"))
        db.execute(text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (1, 1, 'One', 'active'), (2, 1, 'Two', 'active')"))
    try:
        yield factory
    finally:
        engine.dispose()


def _memory(db, legacy_id, memory_id, body):
    value = Memory(id=memory_id, legacy_id=legacy_id, canonical_text=body, category="life_event", source_language="english", source_excerpt=body, confidence=.9, status="active", operation_type="explicit_save", explicit_save=True, normalized_fingerprint=f"l17-{memory_id}")
    db.add(value); db.flush(); return value


def _event(db, legacy_id, event_id=None):
    value = LifeEvent(id=event_id or str(uuid4()), legacy_id=legacy_id, admission_key=str(uuid4()), title="Disposable event", event_type="other", origin="human_created", review_state="approved", lifecycle_state="active")
    db.add(value); db.flush(); return value


def _race(factory, operation):
    barrier = threading.Barrier(2)
    def worker(index):
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                result = operation(db, index)
                db.commit()
                return ("ok", result)
            except IntegrityError:
                db.rollback()
                return ("integrity_error", None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        return [future.result(timeout=20) for future in [pool.submit(worker, i) for i in range(2)]]


def test_pg_migration_shape_and_scoped_uniqueness(pg):
    with pg() as db:
        tables = set(db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'" )).scalars())
        assert {"life_events", "life_event_memories", "life_event_evidence", "life_event_entities"} <= tables
        assert db.execute(text("SELECT COUNT(*) FROM pg_constraint WHERE conname IN ('uq_life_events_admission_key','fk_life_event_memories_event_scope','fk_life_event_evidence_evidence_scope','fk_life_event_entities_entity_scope')")).scalar_one() == 4


def test_pg_concurrent_duplicate_event_admission_is_idempotent(pg):
    with pg() as db: _memory(db, 1, 10, "One moved to Pune in 1998."); db.commit()
    def structure(db, _):
        memory = db.scalar(select(Memory).where(Memory.id == 10))
        return TimelineService(db).structure_memory(memory).id
    results = _race(pg, structure)
    with pg() as db:
        assert db.scalar(select(func.count()).select_from(LifeEvent)) == 1
        assert db.scalar(select(func.count()).select_from(LifeEventMemory)) == 1
    assert all(item[0] == "ok" for item in results)


def test_pg_duplicate_event_retry_is_idempotent(pg):
    with pg() as db:
        memory = _memory(db, 1, 11, "One moved to Pune in 1998.")
        service = TimelineService(db); first = service.structure_memory(memory); second = service.structure_memory(memory)
        db.commit()
        assert first.id == second.id and db.scalar(select(func.count()).select_from(LifeEvent)) == 1


def test_pg_canonical_correction_marks_old_support_stale(pg):
    with pg() as db:
        memory = _memory(db, 1, 12, "One moved to Pune in 1998."); service = TimelineService(db); service.structure_memory(memory); db.commit()
        memory.canonical_text = "One moved to Pune in 1999."; memory.updated_at = datetime.now(timezone.utc); service.reconcile_memory(1, 12); db.commit()
        links = db.scalars(select(LifeEventMemory).where(LifeEventMemory.memory_id == 12)).all()
        assert links and all(link.link_state == "active" for link in links)


def test_pg_transaction_rollback_leaves_no_partial_links(pg):
    with pg() as db:
        memory = _memory(db, 1, 13, "One moved to Pune in 1998."); event = _event(db, 1)
        db.add(LifeEventMemory(legacy_id=1, event_id=event.id, memory_id=memory.id, link_role="primary_support"))
        db.flush(); db.rollback()
    with pg() as db: assert db.scalar(select(func.count()).select_from(LifeEventMemory)) == 0


@pytest.mark.parametrize("child", ["memory", "evidence", "entity"])
def test_pg_cross_legacy_links_are_rejected(pg, child):
    with pg() as db:
        first = _event(db, 1); second = _event(db, 2); memory = _memory(db, 2, 20, "Two moved to Pune in 1998."); entity = MemoryEntity(id=30, legacy_id=2, name="Pune", normalized_name="pune", entity_type="place"); db.add(entity); db.flush()
        if child == "memory": row = LifeEventMemory(legacy_id=1, event_id=first.id, memory_id=memory.id, link_role="primary_support")
        elif child == "entity": row = LifeEventEntity(legacy_id=1, event_id=first.id, entity_id=entity.id, role="place")
        else: row = LifeEventEvidence(legacy_id=1, event_id=first.id, evidence_id=str(uuid4()))
        db.add(row)
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()


def test_pg_legacy_delete_cascades_timeline_but_not_other_legacy(pg):
    with pg() as db:
        first = _event(db, 1); second = _event(db, 2); db.commit(); db.execute(text("DELETE FROM legacies WHERE id=1")); db.commit()
        assert db.scalar(select(LifeEvent.id).where(LifeEvent.id == first.id)) is None
        assert db.scalar(select(LifeEvent.id).where(LifeEvent.id == second.id)) == second.id


def test_pg_source_deletion_does_not_make_active_evidence_claim(pg):
    with pg() as db:
        source_id, job_id, artifact_id, evidence_id = [str(uuid4()) for _ in range(4)]
        now = datetime.now(timezone.utc)
        db.add(MediaSource(id=source_id, legacy_id=1, uploader_user_id=1, kind="document", original_filename="x", declared_mime_type="text/plain", declared_size_bytes=1, upload_request_key=str(uuid4()), upload_request_digest="x"*64, upload_expires_at=now))
        db.flush(); db.add(MediaProcessingJob(id=job_id, legacy_id=1, source_id=source_id, generation=1, kind="extract", pipeline_version="test")); db.add(MediaArtifact(id=artifact_id, legacy_id=1, source_id=source_id, generation=1, kind="text", logical_key="text", storage_backend="local", object_key="x")); db.flush()
        evidence = SourceEvidence(id=evidence_id, legacy_id=1, source_id=source_id, generation=1, job_id=job_id, artifact_id=artifact_id, stable_key="x", kind="text_span", text="x"); db.add(evidence); event = _event(db, 1); db.flush(); db.add(LifeEventEvidence(legacy_id=1, event_id=event.id, evidence_id=evidence.id)); db.commit()
        evidence.removed_at = datetime.now(timezone.utc); db.commit()
        assert db.scalar(select(SourceEvidence.removed_at).where(SourceEvidence.id == evidence_id)) is not None


def test_pg_event_id_scope_rejects_foreign_event_id(pg):
    with pg() as db:
        event = _event(db, 1); db.commit()
        with pytest.raises(IntegrityError):
            db.add(LifeEventMemory(legacy_id=2, event_id=event.id, memory_id=_memory(db, 2, 40, "Two moved to Pune in 1998.").id, link_role="primary_support")); db.flush()
        db.rollback()


def test_pg_conflict_review_commit_fences_stale_rebuild(pg):
    with pg() as db:
        first = _memory(db, 1, 50, "One moved to Pune in 1998.")
        second = _memory(db, 1, 51, "One moved to Pune in 1999.")
        service = TimelineService(db); event = service.structure_memory(first); service.structure_memory(second); db.commit()
        event_id = event.id
        assert db.scalar(select(LifeEvent.review_state).where(LifeEvent.id == event_id)) == "conflict"

    resolved = threading.Event()
    def resolve_then_commit():
        with pg() as db:
            row = db.scalar(select(LifeEvent).where(LifeEvent.id == event_id).with_for_update())
            row.review_state = "approved"; row.conflict_json = None; row.conflict_resolved_at = datetime.now(timezone.utc)
            db.commit(); resolved.set()
    def stale_rebuild():
        assert resolved.wait(timeout=10)
        with pg() as db:
            TimelineService(db).rebuild(1); db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve_then_commit), pool.submit(stale_rebuild)]
        [future.result(timeout=20) for future in futures]
    with pg() as db:
        row = db.scalar(select(LifeEvent).where(LifeEvent.id == event_id))
        assert row.review_state == "approved" and row.conflict_json is None
        assert db.scalar(select(func.count()).select_from(LifeEvent).where(LifeEvent.legacy_id == 1)) == 1


def test_pg_source_unavailability_fences_evidence_attachment(pg):
    with pg() as db:
        memory = _memory(db, 1, 60, "One moved to Pune in 1998.")
        source_id, job_id, artifact_id, evidence_id = [str(uuid4()) for _ in range(4)]
        now = datetime.now(timezone.utc)
        db.add(MediaSource(id=source_id, legacy_id=1, uploader_user_id=1, kind="document", original_filename="x", declared_mime_type="text/plain", declared_size_bytes=1, upload_request_key=str(uuid4()), upload_request_digest="y"*64, upload_expires_at=now)); db.flush()
        db.add(MediaProcessingJob(id=job_id, legacy_id=1, source_id=source_id, generation=1, kind="extract", pipeline_version="test")); db.add(MediaArtifact(id=artifact_id, legacy_id=1, source_id=source_id, generation=1, kind="text", logical_key="text", storage_backend="local", object_key="race")); db.flush()
        db.add(SourceEvidence(id=evidence_id, legacy_id=1, source_id=source_id, generation=1, job_id=job_id, artifact_id=artifact_id, stable_key="race", kind="text_span", text="race")); event = TimelineService(db).structure_memory(memory); db.commit(); event_id = event.id
    barrier = threading.Barrier(2)
    def attach():
        with pg() as db:
            evidence = db.scalar(select(SourceEvidence).where(SourceEvidence.id == evidence_id, SourceEvidence.removed_at.is_(None)))
            barrier.wait(timeout=10)
            if evidence is not None:
                db.add(LifeEventEvidence(legacy_id=1, event_id=event_id, evidence_id=evidence_id, link_state="available"))
            db.commit()
    def remove():
        with pg() as db:
            barrier.wait(timeout=10)
            db.execute(text("UPDATE source_evidence SET removed_at=NOW() WHERE id=:id"), {"id": evidence_id}); db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        [future.result(timeout=20) for future in [pool.submit(attach), pool.submit(remove)]]
    with pg() as db:
        row = db.scalar(select(LifeEvent).where(LifeEvent.id == event_id))
        assert row.lifecycle_state == "active"
        assert serialize_event(db, row)["source_count"] == 0
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.legacy_id == 1)) == 1
