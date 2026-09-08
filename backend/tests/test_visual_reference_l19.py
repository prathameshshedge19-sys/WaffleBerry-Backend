"""L19 isolated L16 intake contracts; synthetic images and independent providers."""

import asyncio
import hashlib
import io
import json
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.collaboration import LegacyCollaborator
from app.models.legacy import Legacy
from app.models.media_intelligence import MemorySourceLink, SourceEvidence, SourceMemoryCandidate
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource
from app.models.user import User
from app.schemas.media_intelligence import SourceAnalysis, SourceCandidateProposal
from app.schemas.media_source import SourceCreate
from app.services.media_intelligence import MediaIntelligenceService
from app.services.media_sources import MediaSourceService, request_digest, serialize_source, utcnow
from app.services.media_storage import LocalSourceStorage
from app.services.media_worker import MediaIntelligenceWorker
from app.services import visual_reference as visual


class IndependentProvider:
    model = "independent-l19-test"

    def __init__(self):
        self.calls = []

    async def analyze(self, legacy, source_kind, evidence):
        self.calls.append(source_kind)
        return SourceAnalysis(source_language="english", candidates=[SourceCandidateProposal(
            canonical_text="Asha enjoyed gardening.", category="other", confidence=.8,
            evidence_indexes=[0], source_language="english")])

    async def analyze_image(self, legacy, image, mime_type):
        return await self.analyze(legacy, "image", [])


@pytest.fixture
def visual_db(tmp_path):
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = LocalSourceStorage(str(tmp_path / "objects"))
    with factory.begin() as db:
        db.add_all([User(id=1, full_name="Owner", email="l19-owner@example.com", password_hash="x"),
                    User(id=2, full_name="Collaborator", email="l19-collab@example.com", password_hash="x")])
        db.add(Legacy(id=1, owner_user_id=1, subject_name="Asha", setup_status="active"))
        db.add(LegacyCollaborator(id=1, legacy_id=1, user_id=2, role="collaborator", status="active"))
    yield factory, storage
    engine.dispose()


def picture(fmt="PNG", size=(256, 192), **options):
    with Image.new("RGB", size, "steelblue") as image:
        output = io.BytesIO()
        image.save(output, format=fmt, **options)
        return output.getvalue()


def reserve(factory, storage, data, purpose="visual_reference", user_id=1, key=None, kind="image", mime="image/png"):
    with factory() as db:
        source = MediaSourceService(storage=storage).create(db, db.get(User, user_id), 1,
            kind=kind, filename="synthetic.png", mime_type=mime, size_bytes=len(data),
            upload_request_key=key or str(uuid4()), processing_purpose=purpose)
        return source.id


def receive(factory, storage, source_id, data):
    with factory() as db:
        return MediaSourceService(storage=storage).receive(db, db.get(User, 1), 1, source_id, data)


def snapshot(factory):
    # Snapshot values, not just counts: includes personality/progression,
    # relationships, canonical graphs, conversations and effect receipts.
    with factory() as db:
        return {table.name: sorted((repr(tuple(row)) for row in db.execute(select(table))))
                for table in Base.metadata.tables.values()
                if table.name not in {"media_sources", "media_artifacts", "media_processing_jobs"}}


@pytest.fixture
def deterministic_decoder(monkeypatch):
    # Explicit test adapter: never represents Windows as OS-confined.
    monkeypatch.setattr(visual, "_run_decoder", visual._decode_image)


def forbidden_provider(*args, **kwargs):
    pytest.fail("visual operation initialized an intelligence provider")


@pytest.mark.parametrize("setup_status", ["active", "collecting_identity"])
def test_visual_lifecycle_zero_effects_and_lazy_provider(visual_db, deterministic_decoder, monkeypatch, setup_status):
    factory, storage = visual_db
    with factory.begin() as db:
        legacy = db.get(Legacy, 1)
        legacy.setup_status = setup_status
        if setup_status == "collecting_identity":
            legacy.subject_name = None
    monkeypatch.setattr("app.services.media_intelligence.get_source_analysis_provider", forbidden_provider)
    before = snapshot(factory)
    data = picture()
    source_id = reserve(factory, storage, data)
    assert snapshot(factory) == before
    receive(factory, storage, source_id, data)
    assert snapshot(factory) == before
    with factory() as db:
        assert db.get(MediaSource, source_id).safety_state == "pending"
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    try:
        assert worker.intelligence is None
        assert worker.run_once() == "visual_reference_validated"
        assert worker.intelligence is None
        assert snapshot(factory) == before
        with factory() as db:
            source = db.get(MediaSource, source_id)
            assert source.state == "ready" and source.safety_state == "clean"
            assert source.metadata_json == {"visual_reference": {"width": 256, "height": 192, "mime": "image/png"}}
            MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
        assert snapshot(factory) == before
        assert worker.run_once() == "purged"
        assert snapshot(factory) == before
        assert worker.run_once() == "idle"
    finally:
        worker.close()


def test_failed_visual_retry_never_extracts(visual_db, deterministic_decoder, monkeypatch):
    factory, storage = visual_db
    monkeypatch.setattr("app.services.media_intelligence.get_source_analysis_provider", forbidden_provider)
    before = snapshot(factory)
    data = b"\x89PNG\r\n\x1a\ncorrupt"
    source_id = reserve(factory, storage, data)
    receive(factory, storage, source_id, data)
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    try:
        for attempt in range(2):
            assert worker.run_once() == "failed"
            with factory() as db:
                source = db.get(MediaSource, source_id)
                assert source.last_error_code == "visual_image_invalid"
                assert source.safety_state == "rejected"
                if attempt == 0:
                    MediaSourceService(storage=storage).retry(db, db.get(User, 1), 1, source_id)
        assert snapshot(factory) == before
    finally:
        worker.close()


def test_ordinary_image_still_extracts_and_invalidates(visual_db, monkeypatch):
    factory, storage = visual_db
    data = picture()
    source_id = reserve(factory, storage, data, purpose="source_review")
    receive(factory, storage, source_id, data)
    provider = IndependentProvider()
    made = []
    monkeypatch.setattr("app.services.media_intelligence.get_source_analysis_provider", lambda: made.append(True) or provider)
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    assert made == []
    try:
        assert worker.run_once() == "candidates_ready"
        assert made == [True] and provider.calls == ["image"]
        invalidations = []
        monkeypatch.setattr("app.services.personality_invalidation.invalidate_in_transaction", lambda connection, ids: invalidations.append(ids))
        with factory() as db:
            assert db.scalar(select(SourceEvidence).where(SourceEvidence.source_id == source_id)) is not None
            assert db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.source_id == source_id)) is not None
            MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
        assert invalidations == [[1]]
    finally:
        worker.close()


def test_purpose_authorization_immutability_and_historical_replay(visual_db):
    factory, storage = visual_db
    data = picture()
    key = str(uuid4())
    source_id = reserve(factory, storage, data, purpose="source_review", key=key)
    old_digest = hashlib.sha256(f"image\nsynthetic.png\nimage/png\n{len(data)}".encode()).hexdigest()
    assert old_digest == request_digest("image", "synthetic.png", "image/png", len(data))
    assert old_digest != request_digest("image", "synthetic.png", "image/png", len(data), "visual_reference")
    assert reserve(factory, storage, data, purpose="source_review", key=key) == source_id
    with pytest.raises(HTTPException) as error:
        reserve(factory, storage, data, key=key)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        reserve(factory, storage, data, user_id=2)
    assert error.value.status_code == 403
    assert reserve(factory, storage, data, purpose="source_review", user_id=2)
    with pytest.raises(HTTPException) as error:
        reserve(factory, storage, b"hello", kind="document", mime="text/plain")
    assert error.value.status_code == 422
    with factory() as db:
        source = db.get(MediaSource, source_id)
        source.processing_purpose = "visual_reference"
        with pytest.raises(ValueError, match="immutable"):
            db.commit()
        db.rollback()
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
    assert reserve(factory, storage, data, purpose="source_review", key=key) == source_id
    with factory() as db:
        assert db.get(MediaSource, source_id).state == "deleting"


def test_schema_defaults_and_purpose_serialization(visual_db):
    factory, storage = visual_db
    dto = SourceCreate(kind="image", filename="photo.png", mime_type="image/png", size_bytes=10, upload_request_key=uuid4())
    assert dto.processing_purpose == "source_review"
    source_id = reserve(factory, storage, picture())
    with factory() as db:
        assert serialize_source(db.get(MediaSource, source_id))["processing_purpose"] == "visual_reference"


def test_direct_intelligence_and_persist_reject_special_purpose(visual_db):
    factory, storage = visual_db
    data = picture()
    source_id = reserve(factory, storage, data)
    receive(factory, storage, source_id, data)
    provider = IndependentProvider()
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage, provider=provider)
    try:
        job_id, token = worker.claim()
        before = snapshot(factory)
        service = MediaIntelligenceService(provider, storage=storage)
        assert asyncio.run(service.process_claim(factory, job_id, token)) == "purpose_rejected"
        with factory() as db:
            source = db.get(MediaSource, source_id)
            artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.source_id == source_id))
        assert asyncio.run(service._persist(factory, job_id, token, source, artifact, [], SourceAnalysis(source_language="english", candidates=[]))) == "purpose_rejected"
        assert provider.calls == [] and snapshot(factory) == before
    finally:
        worker.close()


@pytest.mark.parametrize("model", [SourceEvidence, SourceMemoryCandidate, MemorySourceLink])
def test_direct_factual_admission_is_rejected(visual_db, model):
    factory, storage = visual_db
    source_id = reserve(factory, storage, picture())
    before = snapshot(factory)
    with factory() as db:
        db.add(model(id=str(uuid4()), legacy_id=1, source_id=source_id))
        with pytest.raises(ValueError, match="visual_reference_has_no_factual_support"):
            db.flush()
    assert snapshot(factory) == before


@pytest.mark.parametrize("race", ["delete", "expire", "new_token"])
def test_visual_completion_fences_races(visual_db, monkeypatch, race):
    factory, storage = visual_db
    data = picture()
    source_id = reserve(factory, storage, data)
    receive(factory, storage, source_id, data)
    before = snapshot(factory)

    def decode(data, **kwargs):
        with factory() as db:
            if race == "delete":
                MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
            else:
                job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id == source_id))
                if race == "expire":
                    job.lease_expires_at = utcnow() - timedelta(seconds=1)
                else:
                    job.lease_token = str(uuid4())
                db.commit()
        return {"width": 256, "height": 192, "mime": "image/png"}

    monkeypatch.setattr(visual, "validate_visual_reference", decode)
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    try:
        assert worker.run_once() == "stale"
        assert snapshot(factory) == before
        with factory() as db:
            assert db.get(MediaSource, source_id).state != "ready"
    finally:
        worker.close()


@pytest.mark.parametrize("fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_full_decode_formats_and_truncation(fmt, mime):
    data = picture(fmt)
    assert visual._decode_image(data) == {"width": 256, "height": 192, "mime": mime}
    with pytest.raises(visual.VisualReferenceError):
        visual._decode_image(data[:len(data) // 2])
    for missing in (1, 2, 8, 12):
        with pytest.raises(visual.VisualReferenceError):
            visual._decode_image(data[:-missing])


@pytest.mark.parametrize("data,code", [(b"", "visual_image_empty"), (b"x" * (visual.MAX_BYTES + 1), "visual_image_too_large"), (b"not an image", "visual_image_invalid")], ids=["empty", "oversize", "corrupt"])
def test_invalid_byte_inputs(data, code):
    with pytest.raises(visual.VisualReferenceError, match=code):
        visual._decode_image(data)


@pytest.mark.parametrize("size", [(8193, 1), (1, 8193), (6000, 4001)])
def test_dimension_bounds(size):
    with pytest.raises(visual.VisualReferenceError, match="visual_image_dimensions"):
        visual._decode_image(picture(size=size))


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_multiple_frames_rejected(fmt):
    output = io.BytesIO()
    with Image.new("RGB", (256, 256), "red") as first, Image.new("RGB", (256, 256), "blue") as second:
        first.save(output, format=fmt, save_all=True, append_images=[second], duration=100, loop=0)
    with pytest.raises(visual.VisualReferenceError, match="visual_image_frames"):
        visual._decode_image(output.getvalue())


def test_exif_rotation_crop_and_metadata_strip():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private caption"
    data = picture(size=(256, 128), exif=exif)
    assert visual._decode_image(data) == {"width": 128, "height": 256, "mime": "image/png"}
    crop = {"x": .5, "y": 0, "width": .5, "height": 1, "rotation": 90}
    result = visual._decode_image(data, crop)
    with Image.open(io.BytesIO(result)) as normalized:
        assert normalized.size == (512, 512) and normalized.format == "PNG"
        assert not normalized.getexif() and not normalized.info


@pytest.mark.parametrize("size,rotation,crop_width,crop_height", [
    ((384, 256), 0, 2/3, 1), ((384, 256), 90, 1, 2/3),
    ((256, 384), 180, 1, 2/3), ((256, 384), 270, 2/3, 1),
])
def test_rectangular_source_requires_physical_square(size, rotation, crop_width, crop_height):
    crop = dict(x=0, y=0, width=crop_width, height=crop_height, rotation=rotation)
    output = visual._decode_image(picture(size=size), crop)
    with Image.open(io.BytesIO(output)) as image:
        assert image.size == (512, 512)
    with pytest.raises(visual.VisualReferenceError, match="visual_crop_not_square"):
        visual._decode_image(picture(size=size), dict(crop, width=1, height=1))


def test_square_tolerance_is_deterministic_without_stretching():
    data = picture(size=(256, 256))
    crop = dict(x=0, y=0, width=1, height=1-1e-10, rotation=0)
    assert visual._decode_image(data, crop)
    with pytest.raises(visual.VisualReferenceError, match="visual_crop_not_square"):
        visual._decode_image(data, dict(crop, height=0.9999))


@pytest.mark.parametrize("changes", [{"x": float("nan")}, {"width": float("inf")}, {"x": -.1}, {"x": .75}, {"width": 0}, {"width": .49}, {"rotation": 45}, {"rotation": True}])
def test_crop_rejects_unsafe_or_small_regions(changes):
    crop = {"x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0, **changes}
    with pytest.raises(visual.VisualReferenceError):
        visual._decode_image(picture(size=(256, 256)), crop)


def test_public_decoder_child_transport_scrubs_environment(monkeypatch):
    monkeypatch.setattr(visual.sys, "platform", "linux")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")

    def run(command, **kwargs):
        assert command[1] == "-I"
        assert kwargs["env"] == {} and kwargs["close_fds"] is True
        assert kwargs["timeout"] == visual.DECODE_TIMEOUT_SECONDS
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["input"] == b"synthetic-input"
        kwargs["stdout"].write(json.dumps({"width": 256, "height": 192, "mime": "image/png"}).encode())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(visual.subprocess, "run", run)
    assert visual.validate_visual_reference(b"synthetic-input")["width"] == 256


def test_decoder_fails_closed_without_confinement(monkeypatch):
    monkeypatch.setattr(visual.sys, "platform", "win32")
    with pytest.raises(visual.VisualReferenceError, match="visual_decoder_isolation_unavailable"):
        visual.validate_visual_reference(picture())


@pytest.mark.parametrize("failure", ["timeout", "exit", "bad_output"])
def test_child_failure_is_sanitized(monkeypatch, failure):
    monkeypatch.setattr(visual.sys, "platform", "linux")

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, visual.DECODE_TIMEOUT_SECONDS)
        if failure == "bad_output":
            kwargs["stdout"].write(b"private decoder error details")
        return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0)

    monkeypatch.setattr(visual.subprocess, "run", run)
    with pytest.raises(visual.VisualReferenceError) as error:
        visual.validate_visual_reference(picture())
    assert error.value.code == ("visual_decoder_timeout" if failure == "timeout" else "visual_decoder_failed")


def test_reservation_claim_waits_for_upload_without_provider(visual_db, monkeypatch):
    factory, storage = visual_db
    source_id = reserve(factory, storage, picture())
    monkeypatch.setattr("app.services.media_intelligence.get_source_analysis_provider", forbidden_provider)
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    try:
        assert worker.run_once() == "awaiting_upload"
        with factory() as db:
            assert db.get(MediaSource, source_id).state == "uploading"
    finally:
        worker.close()


def test_storage_checksum_mismatch_never_decodes(visual_db, monkeypatch):
    factory, storage = visual_db
    data = picture()
    source_id = reserve(factory, storage, data)
    receive(factory, storage, source_id, data)
    before = snapshot(factory)
    monkeypatch.setattr(storage, "open", lambda key: io.BytesIO(picture(size=(512, 512))))
    monkeypatch.setattr(visual, "validate_visual_reference", forbidden_provider)
    worker = MediaIntelligenceWorker(sessions=factory, storage=storage)
    try:
        assert worker.run_once() == "failed"
        with factory() as db:
            assert db.get(MediaSource, source_id).last_error_code == "visual_original_mismatch"
        assert snapshot(factory) == before
    finally:
        worker.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Actual libseccomp child requires Linux; no Windows sandbox claim")
def test_real_confined_decoder_and_normalization():
    for fmt in ("PNG", "JPEG", "WEBP"):
        assert visual.validate_visual_reference(picture(fmt))["width"] == 256
    exif = Image.Exif()
    exif[274] = 6
    assert visual.validate_visual_reference(picture(exif=exif))["height"] == 256
    result = visual.normalize_crop(picture(size=(256, 256)), {"x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0})
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (512, 512)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual syscall denial requires Linux")
def test_real_confinement_denies_network_and_file_opens():
    script = (
        "import os, runpy, socket\n"
        f"module = runpy.run_path({str(visual.Path(visual.__file__).resolve())!r})\n"
        "module['_confine_decoder']()\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "for operation in (lambda: open('/etc/hosts', 'rb'), lambda: socket.socket()):\n"
        "    try: operation()\n"
        "    except PermissionError: pass\n"
        "    else: raise AssertionError('confinement bypass')\n"
        "print('confined')\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script], env={}, close_fds=True,
                            capture_output=True, timeout=visual.DECODE_TIMEOUT_SECONDS)
    assert result.returncode == 0 and result.stdout.strip() == b"confined"
