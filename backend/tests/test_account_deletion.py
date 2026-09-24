"""Synthetic account erasure evidence; never accesses a deployed service."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.models.account_deletion import AccountDeletion, AccountDeletionReauth
from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource
from app.models.memory import Memory
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceJob, VoiceProfile, VoiceProfileVersion
from app.services.account_deletion import finalize_account, now, request_account_deletion
from app.services.account_deletion_worker import AccountDeletionWorker
from app.services.legacy_deletion import finalize_one
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage, StorageError
from app.services.media_worker import MediaWorker
from app.services.security import create_access_token
from tests.conftest import register_user
from tests.test_media_sources_l16 import media_db, _reserve
from tests.voice_l21_helpers import reserve, prepare, activate


@pytest.fixture(autouse=True)
def isolated_account_scratch(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("VOICE_TEMP_PATH", str(tmp_path / "private-voice"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def headers(auth):
    return {"Authorization": "Bearer " + auth["access_token"]}


def proof(client, auth):
    result = client.post("/api/v1/account/deletion/reauth", headers=headers(auth), json={"password": "strong-pass-123"})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()["reauth_token"]


def submit(client, auth, token):
    return client.post("/api/v1/account/deletion", headers=headers(auth), json={"confirmation": "DELETE", "reauth_token": token})


def test_api_reauth_confirmation_revocation_idempotence_and_final_removal(test_context, tmp_path):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes)
    cookie = client.cookies.get("legarya_refresh")
    assert client.get("/api/v1/account/deletion").status_code == 401
    assert client.get("/api/v1/account/deletion", headers=headers(auth)).json()["reauth_required"]
    assert submit(client, auth, "x" * 43).status_code == 403
    token = proof(client, auth)
    assert client.post("/api/v1/account/deletion", headers=headers(auth), json={"confirmation": "delete", "reauth_token": token}).status_code == 422
    assert client.post("/api/v1/account/deletion", headers=headers(auth), json={"confirmation": "DELETE", "reauth_token": token, "user_id": 999}).status_code == 422
    result = submit(client, auth, token)
    assert result.status_code == 202, result.text
    assert result.json()["status"] == "deleting"
    assert submit(client, auth, token).status_code == 202
    assert submit(client, auth, "x" * 43).status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers(auth)).status_code == 401
    client.cookies.set("legarya_refresh", cookie)
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": auth["user"]["email"], "password": "strong-pass-123"}).status_code == 401
    with sessions() as db:
        row = db.scalar(select(AccountDeletion))
        deletion_id = row.id
        assert db.scalar(select(func.count()).select_from(AccountDeletion)) == 1
        assert db.get(User, auth["user"]["id"]).deletion_requested_at is not None
    storage = LocalSourceStorage(str(tmp_path / "objects"))
    assert finalize_account(sessions, storage, deletion_id) == "completed"
    assert finalize_account(sessions, storage, deletion_id) == "completed"
    with sessions() as db:
        assert db.get(User, auth["user"]["id"]) is None
        row = db.get(AccountDeletion, deletion_id)
        assert row.user_id is None and row.request_proof_hash is None and row.session_hash is None


def test_reauth_expiry_binding_rate_limit_and_no_sensitive_echo(test_context):
    client, sessions, codes, _ = test_context
    first = register_user(client, codes, email="first@example.com")
    second = register_user(client, codes, email="second@example.com")
    token = proof(client, first)
    assert submit(client, second, token).status_code == 403
    with sessions.begin() as db:
        db.get(AccountDeletionReauth, first["user"]["id"]).expires_at = now() - timedelta(seconds=1)
    assert submit(client, first, token).status_code == 403
    for _ in range(4):
        response = client.post("/api/v1/account/deletion/reauth", headers=headers(first), json={"password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/v1/account/deletion/reauth", headers=headers(first), json={"password": "strong-pass-123"}).status_code == 429
    response = client.post("/api/v1/account/deletion/reauth", headers=headers(second), json={"password": "SECRET" * 100})
    assert response.status_code == 422 and "SECRET" not in response.text


def test_atomic_rollback_never_leaves_partial_request(media_db):
    sessions, storage, _ = media_db
    with pytest.raises(RuntimeError):
        with sessions.begin() as db:
            request_account_deletion(db, 1, verified_support=True)
            raise RuntimeError("synthetic rollback")
    with sessions() as db:
        assert db.get(User, 1).deletion_requested_at is None
        assert db.get(Legacy, 1).deletion_requested_at is None
        assert db.scalar(select(AccountDeletion.id)) is None


def test_shared_private_upload_erased_without_deleting_other_owner(media_db):
    sessions, storage, _ = media_db
    a_source = _reserve(sessions, storage, user_id=2)
    b_source = _reserve(sessions, storage, user_id=1)
    for source_id, actor in ((a_source, 2), (b_source, 1)):
        with sessions() as db:
            MediaSourceService(storage).receive(db, db.get(User, actor), 1, source_id, b"Hello")
    with sessions.begin() as db:
        db.add_all([Conversation(id=100, user_id=2, legacy_id=1, title="private", mode="legacy"),
                    Conversation(id=101, user_id=1, legacy_id=1, title="keep", mode="legacy")])
        db.flush()
        db.add_all([Message(conversation_id=100, role="assistant", content="private A"),
                    Message(conversation_id=101, role="assistant", content="private B")])
        request = request_account_deletion(db, 2, verified_support=True)
        deletion_id = request.id
    assert finalize_account(sessions, storage, deletion_id) == "waiting_for_purge"
    assert MediaWorker(sessions, storage, purge_only=True).run_once() == "purged"
    assert finalize_account(sessions, storage, deletion_id) == "completed"
    with sessions() as db:
        assert db.get(User, 2) is None
        assert db.get(User, 1) is not None and db.get(Legacy, 1) is not None
        assert db.get(MediaSource, a_source) is None and db.get(MediaSource, b_source) is not None
        assert db.get(Conversation, 100) is None and db.get(Conversation, 101) is not None
        assert db.scalar(select(LegacyCollaborator.id).where(LegacyCollaborator.user_id == 2)) is None


def test_owned_multiple_legacies_flags_off_restart_storage_failure(media_db, monkeypatch):
    sessions, storage, settings = media_db
    source_id = _reserve(sessions, storage)
    with sessions() as db:
        MediaSourceService(storage).receive(db, db.get(User, 1), 1, source_id, b"Hello")
    with sessions.begin() as db:
        db.add(Legacy(id=10, owner_user_id=1, subject_name="Second", setup_status="active"))
        request_account_deletion(db, 1, verified_support=True)
    for field in ("voice_cloning_enabled", "voice_enrollment_enabled", "voice_message_playback_enabled", "voice_live_enabled", "media_enabled"):
        monkeypatch.setattr(settings, field, False)
    delete = storage.delete
    monkeypatch.setattr(storage, "delete", lambda *a, **k: (_ for _ in ()).throw(StorageError("synthetic")))
    worker = AccountDeletionWorker(sessions, storage)
    worker.run_once()
    worker.close()
    with sessions.begin() as db:
        assert db.get(User, 1).deletion_requested_at is not None
        assert db.get(MediaSource, source_id).state != "deleted"
        db.execute(update(AccountDeletion).values(next_attempt_at=None))
        db.execute(update(MediaProcessingJob).where(MediaProcessingJob.kind == "purge").values(next_attempt_at=None))
    monkeypatch.setattr(storage, "delete", delete)
    restarted = AccountDeletionWorker(sessions, storage)
    try:
        assert restarted.run_once() == "completed"
    finally:
        restarted.close()
    with sessions() as db:
        assert db.get(User, 1) is None
        assert db.get(Legacy, 1) is None and db.get(Legacy, 10) is None
        assert db.get(Legacy, 2) is not None and db.get(User, 3) is not None


def test_false_delete_acknowledgement_cannot_finalize(media_db, monkeypatch):
    sessions, storage, _ = media_db
    source_id = _reserve(sessions, storage, user_id=2)
    with sessions() as db:
        MediaSourceService(storage).receive(db, db.get(User, 2), 1, source_id, b"Hello")
    with sessions.begin() as db:
        deletion_id = request_account_deletion(db, 2, verified_support=True).id
    monkeypatch.setattr(storage, "delete", lambda *a, **k: None)
    assert MediaWorker(sessions, storage, purge_only=True).run_once() == "retry_wait"
    assert finalize_account(sessions, storage, deletion_id) == "waiting_for_purge"
    with sessions() as db:
        assert db.get(User, 2) is not None
        assert db.get(MediaSource, source_id).state == "deleting"


def test_unconfirmed_s3_write_is_pending_not_assumed_erased(media_db, monkeypatch):
    sessions, storage, _ = media_db
    source_id = _reserve(sessions, storage, user_id=2)
    with sessions.begin() as db:
        artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.source_id == source_id))
        artifact.storage_backend = "s3"
        assert artifact.sha256 is None
        deletion_id = request_account_deletion(db, 2, verified_support=True).id
    monkeypatch.setattr(storage, "backend_name", "s3")
    monkeypatch.setattr(storage, "delete", lambda *args, **kwargs: pytest.fail("Unresolved write must not be called absent"))
    assert MediaWorker(sessions, storage, purge_only=True).run_once() == "retry_wait"
    assert finalize_account(sessions, storage, deletion_id) == "waiting_for_purge"
    with sessions() as db:
        assert db.get(User, 2).deletion_requested_at is not None
        assert db.get(MediaSource, source_id).purged_at is None


def test_final_account_row_waits_for_runtime_absence(media_db, monkeypatch):
    from app.services import voice_runtime_cleanup
    sessions, storage, _ = media_db
    with sessions.begin() as db:
        deletion_id = request_account_deletion(db, 2, verified_support=True).id
    monkeypatch.setattr(voice_runtime_cleanup, "cleanup_voice_runtime", lambda: None)
    assert finalize_account(sessions, storage, deletion_id) == "waiting_for_purge"
    with sessions() as db:
        assert db.get(User, 2).deletion_requested_at is not None
    monkeypatch.setattr(voice_runtime_cleanup, "cleanup_voice_runtime", lambda: 0)
    assert finalize_account(sessions, storage, deletion_id) == "completed"


def test_stale_authenticated_mutation_cannot_publish(media_db):
    sessions, storage, _ = media_db
    with sessions() as stale:
        actor = stale.get(User, 2)
        stale.info["account_actor_id"] = 2
        with sessions.begin() as db:
            request_account_deletion(db, 2, verified_support=True)
        actor.full_name = "late write"
        with pytest.raises(HTTPException) as error:
            stale.commit()
        assert error.value.status_code == 401


def test_google_re_signup_is_new_identity(test_context, tmp_path, monkeypatch):
    from app.services.google_identity import GoogleIdentity
    from app.api.routes import auth as routes
    client, sessions, codes, _ = test_context
    identity = GoogleIdentity(sub="synthetic-google", email="google@example.com", name="Synthetic")
    monkeypatch.setattr(routes, "verify_google_credential", lambda _: identity)
    first = client.post("/api/v1/auth/google", json={"credential": "synthetic" * 5, "accepted_terms": True}).json()
    with sessions.begin() as db:
        deletion_id = request_account_deletion(db, first["user"]["id"], verified_support=True).id
    assert client.post("/api/v1/auth/google", json={"credential": "synthetic" * 5, "accepted_terms": True}).status_code == 401
    assert finalize_account(sessions, LocalSourceStorage(str(tmp_path / "objects")), deletion_id) == "completed"
    second = client.post("/api/v1/auth/google", json={"credential": "synthetic" * 5, "accepted_terms": True}).json()
    assert second["user"]["id"] != first["user"]["id"]
    assert client.get("/api/v1/auth/me", headers=headers(first)).status_code == 401
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Legacy)) == 0


def test_shared_voice_only_requester_audio_is_purged(media_db):
    import hashlib
    from app.services.voice_profiles import VoiceJobService, canonical_digest
    sessions, storage, _ = media_db
    with sessions() as db:
        version = prepare(db, reserve(db))
        activate(db, version)
        profile_id, version_id = version.voice_profile_id, version.id
        job = VoiceJobService().enqueue(db, legacy_id=1, profile_id=profile_id, version_id=version_id,
            kind="synthesize", request_key="account-shared", request_digest=canonical_digest({"account": "shared"}),
            purpose="preview", authoritative_text="Synthetic", authoritative_text_digest=hashlib.sha256(b"Synthetic").hexdigest(),
            model_manifest_digest="0" * 64, inference_config_digest="0" * 64, requested_by_user_id=2)
        db.commit()
        job_id = job.id
    with sessions.begin() as db:
        deletion_id = request_account_deletion(db, 2, verified_support=True).id
        assert db.get(VoiceJob, job_id).state == "cancelled"
    assert finalize_account(sessions, storage, deletion_id) == "completed"
    with sessions() as db:
        assert db.get(VoiceJob, job_id) is None
        assert db.get(VoiceProfile, profile_id).current_version_id == version_id
        assert db.get(VoiceProfileVersion, version_id).status == "ready"


def test_google_reauth_requires_fresh_verified_exact_identity(test_context, monkeypatch):
    from app.services import google_identity
    from app.config import get_settings
    from app.services.google_identity import GoogleIdentity
    from app.api.routes import auth as routes
    import time
    client, sessions, codes, _ = test_context
    identity = GoogleIdentity(sub="synthetic-google-proof", email="proof@example.com", name="Synthetic")
    monkeypatch.setattr(routes, "verify_google_credential", lambda _: identity)
    auth = client.post("/api/v1/auth/google", json={"credential":"synthetic"*5,"accepted_terms":True}).json()
    monkeypatch.setattr(get_settings(), "google_web_client_id", "synthetic-client-id")
    claims = {"sub":identity.sub,"email":identity.email,"name":identity.name,"email_verified":True,
              "iss":"https://accounts.google.com","iat":int(time.time())-600}
    monkeypatch.setattr(google_identity.id_token,"verify_oauth2_token",lambda *args,**kwargs:dict(claims))
    path="/api/v1/account/deletion/reauth"
    assert client.post(path,headers=headers(auth),json={"credential":"synthetic"*5}).status_code==401
    claims["iat"]=int(time.time());claims["sub"]="other-google"
    assert client.post(path,headers=headers(auth),json={"credential":"synthetic"*5}).status_code==401
    claims["sub"]=identity.sub
    result=client.post(path,headers=headers(auth),json={"credential":"synthetic"*5})
    assert result.status_code==200
    assert submit(client,auth,result.json()["reauth_token"]).status_code==202


def test_changed_password_invalidates_pending_proof(test_context):
    from app.services.security import hash_password
    client,sessions,codes,_=test_context
    auth=register_user(client,codes)
    token=proof(client,auth)
    with sessions.begin() as db:
        db.get(User,auth["user"]["id"]).password_hash=hash_password("new-test-password")
    assert submit(client,auth,token).status_code==403


def test_already_deleting_owned_legacy_and_canonical_shared_attribution(media_db):
    from app.services.legacy_deletion import request_deletion
    from tests.test_legacy_persona_l6 import add_memory
    sessions,storage,_=media_db
    own=add_memory(sessions,1,"Owned canonical content")
    shared=add_memory(sessions,2,"Canonical content belonging to another Legacy")
    with sessions.begin() as db:
        db.get(Memory,shared).contributor_user_id=1
        db.get(Memory,shared).last_contributor_user_id=1
        request_deletion(db,db.get(User,1),1,"DELETE LEGACY 1",commit=False)
    with sessions.begin() as db:
        deletion_id=request_account_deletion(db,1,verified_support=True).id
    assert finalize_one(sessions,storage)=="legacy_erased"
    assert finalize_account(sessions,storage,deletion_id)=="completed"
    with sessions() as db:
        assert db.get(Memory,own) is None
        canonical=db.get(Memory,shared)
        assert canonical.canonical_text=="Canonical content belonging to another Legacy"
        assert canonical.contributor_user_id is None and canonical.last_contributor_user_id is None


def test_owned_voice_hook_purges_reference_and_never_republishes(media_db):
    from app.services.voice_profiles import StaleVoiceClaim, VoiceJobService
    sessions,storage,_=media_db
    with sessions() as db:
        version=prepare(db,reserve(db)); activate(db,version)
        version_id=version.id
        reference=db.get(VoiceAsset,version.reference_asset_id)
        storage.put(reference.object_key,b"synthetic-reference-fixture",content_type="audio/wav")
    with sessions.begin() as db:
        deletion_id=request_account_deletion(db,1,verified_support=True).id
        assert db.get(VoiceProfileVersion,version_id).status=="purge_pending"
    assert finalize_account(sessions,storage,deletion_id)=="waiting_for_purge"
    worker=AccountDeletionWorker(sessions,storage)
    try:
        assert worker.run_once()=="completed"
    finally:
        worker.close()
    with sessions() as db:
        assert db.get(User,1) is None and db.get(VoiceProfileVersion,version_id) is None
        assert db.scalar(select(VoiceAsset.id)) is None
        with pytest.raises(StaleVoiceClaim):
            VoiceJobService().publish_asset_purge(db,"stale-job","stale-token","stale-asset")


@pytest.mark.parametrize("purpose", ["preview", "message", "live"])
def test_generated_private_speech_waits_for_writer_then_erases_only_actor(media_db, purpose):
    import hashlib
    from app.services.voice_profiles import StaleVoiceClaim, VoiceJobService, canonical_digest
    from app.services.voice_worker import VoiceWorker
    sessions, storage, _ = media_db
    payload = b"synthetic-private-speech"
    with sessions() as db:
        version = prepare(db, reserve(db)); activate(db, version)
        version_id, profile_id = version.id, version.voice_profile_id
        reference = db.get(VoiceAsset, version.reference_asset_id)
        reference_key = reference.object_key
        storage.put(reference_key, b"synthetic-reference-fixture", content_type="audio/wav")
        db.add(Conversation(id=100, user_id=2, legacy_id=1, title="Synthetic", mode="legacy"))
        db.flush()
        message = Message(conversation_id=100, role="assistant", content="Synthetic")
        db.add(message); db.flush()
        job = VoiceJobService().enqueue(db, legacy_id=1, profile_id=profile_id, version_id=version_id,
            kind="synthesize", request_key="private-generated", request_digest=canonical_digest({"purpose": purpose}),
            purpose=purpose, authoritative_text="Synthetic", authoritative_text_digest=hashlib.sha256(b"Synthetic").hexdigest(),
            model_manifest_digest="0" * 64, inference_config_digest="0" * 64, requested_by_user_id=2,
            conversation_id=100, message_id=message.id, realtime_turn_id=1, realtime_claim_token=str(uuid4()))
        job.state, job.lease_token = "running", str(uuid4())
        job.lease_expires_at = now() + timedelta(minutes=2)
        job.writer_deadline = now() + timedelta(minutes=3)
        job_id, stale_token = job.id, job.lease_token
        db.flush()  # Persist the composite parent before the synthetic asset.
        asset_id = str(uuid4())
        object_key = f"legarya/legacies/1/voice/{profile_id}/{version_id}/generated/{asset_id}.wav"
        db.add(VoiceAsset(id=asset_id, legacy_id=1, voice_profile_id=profile_id, version_id=version_id,
            job_id=job_id, kind="generated", state="dispatching", storage_backend="local", object_key=object_key,
            sha256=hashlib.sha256(payload).hexdigest(), byte_count=len(payload), mime_type="audio/wav",
            writer_deadline=job.writer_deadline, created_at=now()))
        db.commit()
        storage.put(object_key, payload, content_type="audio/wav")
    with sessions.begin() as db:
        deletion_id = request_account_deletion(db, 2, verified_support=True).id
        assert db.get(VoiceJob, job_id).state == "cancelled"
        assert db.get(VoiceAsset, asset_id).state == "purge_pending"
    worker = VoiceWorker(sessions, storage, purge_only=True)
    try:
        assert worker.run_once() == "idle"  # The bounded late writer has not expired.
        assert finalize_account(sessions, storage, deletion_id) == "waiting_for_purge"
        with sessions.begin() as db:
            db.get(VoiceAsset, asset_id).writer_deadline = now() - timedelta(seconds=1)
            db.execute(update(VoiceJob).where(VoiceJob.kind == "purge").values(next_attempt_at=now() - timedelta(seconds=1)))
        assert worker.run_once() == "purged"
    finally:
        worker.close()
    with sessions() as db:
        asset = db.get(VoiceAsset, asset_id)
        assert asset.state == "purged" and asset.absence_checks >= 1 and asset.purged_at is not None
        with pytest.raises(StaleVoiceClaim):
            VoiceJobService().heartbeat(db, job_id, stale_token)
    assert finalize_account(sessions, storage, deletion_id) == "completed"
    with sessions() as db:
        assert db.get(User, 2) is None and db.get(User, 1) is not None
        assert db.get(VoiceAsset, asset_id) is None and db.get(VoiceJob, job_id) is None
        assert db.get(VoiceProfile, profile_id).current_version_id == version_id
        assert db.get(VoiceProfileVersion, version_id).status == "ready"
    # The other owner's canonical reference remains, but no generated bytes do.
    files = [path.read_bytes() for path in storage.root.rglob("*") if path.is_file()]
    assert payload not in files and b"synthetic-reference-fixture" in files
