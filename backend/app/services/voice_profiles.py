"""Transactional L21 preserved-voice aggregate and durable job control plane.

Commands own no commits and perform no storage/model I/O while locks are held.
The lock order is Legacy -> profile -> versions -> consents -> jobs -> assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models.legacy import Legacy
from app.models.voice_profile import (
    VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion,
)
from app.services.voice_providers import (
    REFERENCE_RECIPE, ClonedSpeech, PreparedReference, validate_prepared,
    validate_speech,
)

CONSENT_COPY = "l21-voice-consent-v1"
CONSENT_POLICY = "l21-voice-policy-v1"
CONSENT_TEXT = ("I confirm that I have the authority to provide and preserve this voice "
    "for this Legacy, and I consent to LegaRya processing this recording to create synthetic speech.")
CONSENT_TEXT_DIGEST = hashlib.sha256(CONSENT_TEXT.encode("utf-8")).hexdigest()
PREPARATION_RECIPE = REFERENCE_RECIPE
LEASE_SECONDS = 30
WRITER_SECONDS = 120
HEX64 = re.compile(r"^[a-f0-9]{64}$")


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def conflict(code="voice_revision_conflict"):
    raise HTTPException(409, detail={"code": code, "message": "Voice Profile changed. Refresh and try again."})


class StaleVoiceClaim(RuntimeError):
    pass


def _locked(db, model, *conditions):
    db.flush()
    return list(db.scalars(select(model).where(*conditions).order_by(model.id)
        .execution_options(populate_existing=True).with_for_update()))


@dataclass
class VoiceScope:
    legacy: Legacy
    profile: VoiceProfile | None
    versions: dict[str, VoiceProfileVersion]
    consents: dict[str, VoiceConsentReceipt]
    jobs: dict[str, VoiceJob]
    assets: dict[str, VoiceAsset]


def lock_scope(db, legacy_id: int, owner_id: int | None = None, *, locked_legacy: Legacy | None = None) -> VoiceScope:
    if locked_legacy is None:
        rows = _locked(db, Legacy, Legacy.id == legacy_id)
        legacy = rows[0] if rows else None
    else:
        legacy = locked_legacy
    if (legacy is None or legacy.id != legacy_id
            or (owner_id is not None and (legacy.owner_user_id != owner_id or legacy.deletion_requested_at is not None))):
        raise HTTPException(404, detail="Legacy not found.")
    profiles = _locked(db, VoiceProfile, VoiceProfile.legacy_id == legacy_id)
    live = [item for item in profiles if item.status != "deleted"]
    if len(live) > 1:
        raise RuntimeError("voice_profile_scope_corrupt")
    versions = {item.id: item for item in _locked(db, VoiceProfileVersion, VoiceProfileVersion.legacy_id == legacy_id)}
    consents = {item.id: item for item in _locked(db, VoiceConsentReceipt, VoiceConsentReceipt.legacy_id == legacy_id)}
    jobs = {item.id: item for item in _locked(db, VoiceJob, VoiceJob.legacy_id == legacy_id)}
    assets = {item.id: item for item in _locked(db, VoiceAsset, VoiceAsset.legacy_id == legacy_id)}
    return VoiceScope(legacy, live[0] if live else None, versions, consents, jobs, assets)


def _revoke_consent(receipt, now):
    if receipt.revoked_at is None:
        receipt.revoked_at = now


def _schedule_purge(db, scope: VoiceScope, version: VoiceProfileVersion, now) -> VoiceJob | None:
    if version.status == "purged":
        return None
    version.operation_generation += 1
    version.status = "purge_pending"
    version.removed_at = version.removed_at or now
    version.updated_at = now
    for job in scope.jobs.values():
        if (job.version_id == version.id and job.state in {"queued", "running", "retry_wait"}
                and (job.kind != "purge" or job.operation_generation != version.operation_generation)):
            job.state = "cancelled"
            job.lease_token = job.lease_expires_at = job.writer_deadline = None
            job.finished_at = now
    for asset in scope.assets.values():
        if asset.version_id == version.id and asset.state != "purged":
            asset.state = "purge_pending"
            asset.purge_requested_at = asset.purge_requested_at or now
    key = f"purge:{version.id}:{version.operation_generation}"
    existing = next((job for job in scope.jobs.values() if job.voice_profile_id == version.voice_profile_id
        and job.kind == "purge" and job.request_key == key), None)
    if existing:
        return existing
    request_digest = canonical_digest({"legacy_id": version.legacy_id, "profile_id": version.voice_profile_id,
        "version_id": version.id, "generation": version.operation_generation, "kind": "purge"})
    job = VoiceJob(id=str(uuid4()), legacy_id=version.legacy_id, voice_profile_id=version.voice_profile_id,
        version_id=version.id, kind="purge", state="queued", priority=100, attempts=0,
        operation_generation=version.operation_generation, next_attempt_at=now,
        request_key=key, request_digest=request_digest, created_at=now)
    db.add(job)
    scope.jobs[job.id] = job
    return job


def _schedule_asset_purge(db, scope: VoiceScope, asset: VoiceAsset, now,
                          *, not_before=None) -> VoiceJob | None:
    """Queue exact-object erasure without purging an otherwise usable version."""
    if asset.state == "purged":
        return None
    asset.state = "purge_pending"
    asset.purge_requested_at = asset.purge_requested_at or now
    key = f"asset-purge:{asset.id}:{scope.versions[asset.version_id].operation_generation}"
    existing = next((job for job in scope.jobs.values()
        if job.kind == "purge" and job.request_key == key), None)
    if existing:
        return existing
    version = scope.versions[asset.version_id]
    request_digest = canonical_digest({"legacy_id": asset.legacy_id,
        "profile_id": asset.voice_profile_id, "version_id": asset.version_id,
        "asset_id": asset.id, "generation": version.operation_generation,
        "kind": "asset_purge"})
    job = VoiceJob(id=str(uuid4()), legacy_id=asset.legacy_id,
        voice_profile_id=asset.voice_profile_id, version_id=asset.version_id,
        kind="purge", state="queued", priority=100, attempts=0,
        operation_generation=version.operation_generation,
        next_attempt_at=not_before or now, request_key=key,
        request_digest=request_digest, created_at=now)
    db.add(job)
    scope.jobs[job.id] = job
    return job


class VoiceEnrollmentService:
    """Reserve one enrollment attempt and one new human consent receipt."""

    def reserve_intent(self, db, owner_id: int, legacy_id: int, payload):
        scope = lock_scope(db, legacy_id, owner_id)
        if (payload.consent_copy_version != CONSENT_COPY
                or payload.policy_version != CONSENT_POLICY
                or payload.presented_copy_digest != CONSENT_TEXT_DIGEST):
            raise HTTPException(422, detail={"code": "voice_consent_invalid",
                "message": "Voice authorization must be reviewed and accepted again."})
        request = payload.model_dump(mode="json")
        request.pop("expected_revision")
        request_digest = canonical_digest(request)
        key = str(payload.request_key)
        if scope.profile:
            existing = next((v for v in scope.versions.values()
                if v.voice_profile_id == scope.profile.id and v.request_key == key), None)
            if existing:
                if existing.request_digest != request_digest:
                    conflict("voice_request_conflict")
                return existing
        current_revision = scope.profile.revision if scope.profile else 0
        if payload.expected_revision != current_revision:
            conflict()
        now = utcnow()
        profile = scope.profile
        if profile is None:
            profile = VoiceProfile(id=str(uuid4()), legacy_id=legacy_id, revision=1,
                status="processing", created_at=now, updated_at=now)
            db.add(profile)
            db.flush()
        else:
            if profile.status in {"deleting", "deleted"}:
                conflict("voice_deletion_in_progress")
            profile.revision += 1
            profile.updated_at = now
            if profile.current_version_id is None:
                profile.status = "processing"
            profile.revoked_at = None
        old_candidate = scope.versions.get(profile.desired_version_id)
        if old_candidate and old_candidate.id != profile.current_version_id:
            _schedule_purge(db, scope, old_candidate, now)
        receipt = VoiceConsentReceipt(id=str(uuid4()), legacy_id=legacy_id,
            voice_profile_id=profile.id, actor_user_id=owner_id,
            copy_version=payload.consent_copy_version, policy_version=payload.policy_version,
            authority_basis=payload.authority_basis, source_category=payload.source_category,
            presented_copy_digest=payload.presented_copy_digest, accepted_at=now)
        db.add(receipt)
        version = VoiceProfileVersion(id=str(uuid4()), legacy_id=legacy_id,
            voice_profile_id=profile.id,
            version_number=max((v.version_number for v in scope.versions.values()
                if v.voice_profile_id == profile.id), default=0) + 1,
            operation_generation=1, status="uploading", provider_name="indicf5",
            language=payload.language, consent_receipt_id=receipt.id,
            created_by_user_id=owner_id, request_key=key, request_digest=request_digest,
            created_at=now, updated_at=now)
        db.add(version)
        db.flush()
        profile.desired_version_id = version.id
        return version

    def reserve_upload(self, db, owner_id: int, legacy_id: int, version_id: str,
                       *, sha256: str, byte_count: int, mime_type: str, storage,
                       retention_seconds: int, writer_seconds: int = WRITER_SECONDS):
        if (not HEX64.fullmatch(sha256) or type(byte_count) is not int or byte_count <= 0
                or not isinstance(mime_type, str) or len(mime_type) > 127):
            raise ValueError("voice_upload_invalid")
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        version = scope.versions.get(version_id)
        consent = scope.consents.get(version.consent_receipt_id) if version else None
        if (profile is None or version is None or version.voice_profile_id != profile.id
                or profile.desired_version_id != version.id or version.status != "uploading"
                or consent is None or consent.revoked_at is not None
                or consent.actor_user_id != owner_id):
            conflict("voice_upload_changed")
        originals = [asset for asset in scope.assets.values()
            if asset.version_id == version.id and asset.kind == "original"]
        if originals:
            asset = originals[0]
            if (asset.sha256 != sha256 or asset.byte_count != byte_count
                    or asset.mime_type != mime_type):
                conflict("voice_upload_changed")
            if asset.state == "available":
                return asset, False
            conflict("voice_upload_in_progress")
        now = utcnow()
        asset_id = str(uuid4())
        key = (f"legarya/legacies/{legacy_id}/voice/{profile.id}/"
            f"{version.id}/original/{asset_id}")
        asset = VoiceAsset(id=asset_id, legacy_id=legacy_id,
            voice_profile_id=profile.id, version_id=version.id, kind="original",
            state="dispatching", storage_backend=storage.backend_name,
            object_key=key, storage_bucket=storage.bucket_name,
            encryption_key_id=storage.encryption_key_id, sha256=sha256,
            byte_count=byte_count, mime_type=mime_type,
            writer_deadline=now + timedelta(seconds=writer_seconds),
            expires_at=now + timedelta(seconds=retention_seconds), created_at=now)
        db.add(asset)
        db.flush()
        scope.assets[asset.id] = asset
        return asset, True

    def publish_upload(self, db, owner_id: int, legacy_id: int, version_id: str,
                       asset_id: str, stored):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        version = scope.versions.get(version_id)
        asset = scope.assets.get(asset_id)
        consent = scope.consents.get(version.consent_receipt_id) if version else None
        now = utcnow()
        if (profile is None or version is None or asset is None
                or profile.desired_version_id != version.id or version.status != "uploading"
                or asset.version_id != version.id or asset.kind != "original"
                or asset.state != "dispatching" or asset.writer_deadline is None
                or aware(asset.writer_deadline) <= now
                or stored.byte_size != asset.byte_count
                or consent is None or consent.revoked_at is not None
                or consent.actor_user_id != owner_id):
            raise StaleVoiceClaim()
        asset.object_version = stored.version
        asset.state = "available"
        asset.writer_deadline = None
        version.status = "queued"
        version.updated_at = now
        request_digest = canonical_digest({"legacy_id": legacy_id,
            "profile_id": profile.id, "version_id": version.id,
            "generation": version.operation_generation,
            "original_sha256": asset.sha256, "kind": "prepare"})
        job = VoiceJobService().enqueue(db, legacy_id=legacy_id,
            profile_id=profile.id, version_id=version.id, kind="prepare",
            request_key=f"prepare:{version.id}:{version.operation_generation}",
            request_digest=request_digest, priority=40,
            operation_generation=version.operation_generation)
        return version, job

    def fail_upload(self, db, owner_id: int, legacy_id: int, version_id: str,
                    asset_id: str, safe_code: str):
        scope = lock_scope(db, legacy_id, owner_id)
        version = scope.versions.get(version_id)
        asset = scope.assets.get(asset_id)
        if version is None or asset is None or asset.version_id != version.id:
            return None
        now = utcnow()
        if version.status == "uploading":
            version.status = "failed"
            version.safe_failure_code = safe_code
            version.updated_at = now
        not_before = asset.writer_deadline
        return _schedule_asset_purge(db, scope, asset, now, not_before=not_before)


class VoiceJobService:
    def enqueue(self, db, *, legacy_id, profile_id, version_id, kind, request_key,
                request_digest, priority=0, operation_generation=None):
        if (kind not in {"prepare", "synthesize", "purge"}
                or not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128
                or not HEX64.fullmatch(request_digest)):
            raise ValueError("voice_job_request_invalid")
        scope = lock_scope(db, legacy_id)
        profile = scope.profile
        version = scope.versions.get(version_id)
        if profile is None or profile.id != profile_id or version is None or version.voice_profile_id != profile_id:
            raise HTTPException(404, detail="Voice Profile unavailable.")
        existing = next((job for job in scope.jobs.values()
            if job.voice_profile_id == profile_id and job.kind == kind and job.request_key == request_key), None)
        if existing:
            if existing.request_digest != request_digest:
                conflict("voice_job_request_conflict")
            return existing
        generation = operation_generation or version.operation_generation
        job = VoiceJob(id=str(uuid4()), legacy_id=legacy_id, voice_profile_id=profile_id,
            version_id=version_id, kind=kind, state="queued", priority=priority, attempts=0,
            operation_generation=generation, next_attempt_at=utcnow(), request_key=request_key,
            request_digest=request_digest, created_at=utcnow())
        db.add(job)
        return job

    def claim(self, db, kind: str, *, now=None, lease_seconds=LEASE_SECONDS):
        now = now or utcnow()
        eligible = or_(
            (VoiceJob.state == "queued") & or_(VoiceJob.next_attempt_at.is_(None), VoiceJob.next_attempt_at <= now),
            (VoiceJob.state == "retry_wait") & (VoiceJob.next_attempt_at <= now),
            (VoiceJob.state == "running") & (VoiceJob.lease_expires_at <= now),
        )
        row = db.execute(select(Legacy, VoiceJob.id).join(VoiceJob, VoiceJob.legacy_id == Legacy.id)
            .where(VoiceJob.kind == kind, eligible)
            .order_by(VoiceJob.priority.desc(), VoiceJob.created_at, VoiceJob.id)
            .limit(1).with_for_update(of=Legacy, skip_locked=True)).first()
        if row is None:
            return None
        legacy, job_id = row
        scope = lock_scope(db, legacy.id, locked_legacy=legacy)
        job = scope.jobs.get(job_id)
        version = scope.versions.get(job.version_id) if job else None
        profile = scope.profile
        # Recheck after acquiring the common Legacy-first lock. A concurrent
        # claimant may have won between candidate discovery and this lock.
        still_eligible = job and (
            (job.state == "queued" and (job.next_attempt_at is None or aware(job.next_attempt_at) <= now))
            or (job.state == "retry_wait" and job.next_attempt_at is not None and aware(job.next_attempt_at) <= now)
            or (job.state == "running" and job.lease_expires_at is not None and aware(job.lease_expires_at) <= now))
        if not still_eligible:
            return None
        if (legacy is None or (legacy.deletion_requested_at is not None and kind != "purge") or profile is None
                or profile.status in {"revoked", "deleting", "deleted"} and kind != "purge"
                or version is None or version.operation_generation != job.operation_generation
                or version.status == "purged"):
            job.state = "cancelled"
            job.finished_at = now
            job.lease_token = job.lease_expires_at = job.writer_deadline = None
            return None
        if kind == "prepare" and (profile.desired_version_id != version.id or version.status not in {"queued", "preparing"}):
            job.state = "cancelled"
            job.finished_at = now
            return None
        if kind == "synthesize" and (profile.status != "active" or profile.current_version_id != version.id or version.status != "ready"):
            job.state = "cancelled"
            job.finished_at = now
            return None
        if kind == "purge":
            asset_id = job.request_key.split(":", 2)[1] if job.request_key.startswith("asset-purge:") else None
            asset = scope.assets.get(asset_id) if asset_id else None
            if ((asset_id and (asset is None or asset.version_id != version.id
                    or asset.state != "purge_pending"))
                    or (not asset_id and version.status != "purge_pending")):
                job.state = "cancelled"
                job.finished_at = now
                return None
            pending_deadlines = [aware(item.writer_deadline) for item in scope.assets.values()
                if item.version_id == version.id and item.state == "purge_pending"
                and item.writer_deadline is not None and aware(item.writer_deadline) > now
                and (asset_id is None or item.id == asset_id)]
            if pending_deadlines:
                job.state = "retry_wait"
                job.next_attempt_at = max(pending_deadlines)
                job.lease_token = job.lease_expires_at = job.writer_deadline = None
                return None
        job.state = "running"
        job.attempts += 1
        job.lease_token = str(uuid4())
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.writer_deadline = now + timedelta(seconds=WRITER_SECONDS)
        job.started_at = job.started_at or now
        job.safe_error_code = None
        if kind == "prepare":
            version.status = "preparing"
        return job

    def reconcile_expired_original_writes(self, db, *, now=None):
        """Fence web-process crashes after an original reservation commit."""
        now = now or utcnow()
        legacy_ids = list(db.scalars(select(VoiceAsset.legacy_id).where(
            VoiceAsset.kind == "original", VoiceAsset.state == "dispatching",
            VoiceAsset.writer_deadline <= now).distinct().order_by(
                VoiceAsset.legacy_id)))
        reconciled = 0
        for legacy_id in legacy_ids:
            scope = lock_scope(db, legacy_id)
            for asset in scope.assets.values():
                if (asset.kind != "original" or asset.state != "dispatching"
                        or asset.writer_deadline is None
                        or aware(asset.writer_deadline) > now):
                    continue
                version = scope.versions.get(asset.version_id)
                if version is None:
                    continue
                _schedule_asset_purge(db, scope, asset, now)
                if version.status == "uploading":
                    version.status = "failed"
                    version.safe_failure_code = "voice_upload_interrupted"
                    version.updated_at = now
                reconciled += 1
        return reconciled

    def _publication_scope(self, db, job_id, token, now):
        identity = db.execute(select(VoiceJob.legacy_id).where(VoiceJob.id == job_id)).scalar_one_or_none()
        if identity is None:
            raise StaleVoiceClaim()
        scope = lock_scope(db, identity)
        job = scope.jobs.get(job_id)
        version = scope.versions.get(job.version_id) if job else None
        if (job is None or version is None or job.state != "running" or job.lease_token != token
                or job.lease_expires_at is None or aware(job.lease_expires_at) <= now
                or job.operation_generation != version.operation_generation):
            raise StaleVoiceClaim()
        return scope, version, job

    @staticmethod
    def _finish(job, now):
        job.state = "succeeded"
        job.finished_at = now
        job.lease_token = job.lease_expires_at = job.writer_deadline = None

    def reserve_reference(self, db, job_id, token, result: PreparedReference, storage):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        validate_prepared(result, version.operation_generation)
        if (job.kind != "prepare" or scope.profile is None
                or scope.profile.desired_version_id != version.id
                or version.status != "preparing" or not result.reference_audio):
            raise StaleVoiceClaim()
        existing = next((asset for asset in scope.assets.values()
            if asset.version_id == version.id and asset.kind == "reference"
            and asset.state != "purged"), None)
        if existing:
            if (existing.sha256 == result.audio_digest
                    and existing.byte_count == len(result.reference_audio)):
                return existing
            raise StaleVoiceClaim()
        asset_id = str(uuid4())
        key = (f"legarya/legacies/{version.legacy_id}/voice/{version.voice_profile_id}/"
            f"{version.id}/reference/{asset_id}.wav")
        asset = VoiceAsset(id=asset_id, legacy_id=version.legacy_id,
            voice_profile_id=version.voice_profile_id, version_id=version.id,
            job_id=job.id, kind="reference", state="dispatching",
            storage_backend=storage.backend_name, storage_bucket=storage.bucket_name,
            encryption_key_id=storage.encryption_key_id, object_key=key,
            sha256=result.audio_digest, byte_count=len(result.reference_audio),
            mime_type="audio/wav", sample_rate=result.sample_rate,
            channels=result.channels, duration_ms=result.duration_ms,
            writer_deadline=now + timedelta(seconds=WRITER_SECONDS), created_at=now)
        db.add(asset)
        db.flush()
        return asset

    def publish_reference(self, db, job_id, token, result: PreparedReference,
                          *, reference_asset_id, stored, model_manifest,
                          asr_manifest, inference_config):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        asset = scope.assets.get(reference_asset_id)
        if (job.kind != "prepare" or asset is None or asset.job_id != job.id
                or asset.kind != "reference" or asset.state != "dispatching"
                or asset.writer_deadline is None or aware(asset.writer_deadline) <= now
                or stored.byte_size != asset.byte_count
                or asset.sha256 != result.audio_digest):
            raise StaleVoiceClaim()
        asset.object_version = stored.version
        asset.state = "available"
        asset.writer_deadline = None
        return self.publish_prepared(db, job_id, token, result,
            reference_asset_id=reference_asset_id, model_manifest=model_manifest,
            asr_manifest=asr_manifest, inference_config=inference_config)

    def abandon_reference(self, db, *, legacy_id, asset_id, not_before=None):
        return self.schedule_asset_purge(db, legacy_id=legacy_id,
            asset_id=asset_id, not_before=not_before)

    def publish_prepared(self, db, job_id, token, result: PreparedReference, *, reference_asset_id,
                         model_manifest, asr_manifest, inference_config):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        if (job.kind != "prepare" or scope.legacy.deletion_requested_at is not None
                or scope.profile is None or scope.profile.status in {"revoked", "deleting", "deleted"}
                or scope.profile.desired_version_id != version.id or version.status != "preparing"):
            raise StaleVoiceClaim()
        validate_prepared(result, version.operation_generation)
        consent = scope.consents.get(version.consent_receipt_id)
        asset = scope.assets.get(reference_asset_id)
        if (consent is None or consent.revoked_at is not None
                or consent.actor_user_id != version.created_by_user_id
                or asset is None or asset.version_id != version.id or asset.kind != "reference"
                or asset.state != "available" or asset.sha256 != result.audio_digest
                or asset.sample_rate != result.sample_rate or asset.channels != result.channels
                or asset.duration_ms != result.duration_ms):
            raise StaleVoiceClaim()
        asr_manifest = {**asr_manifest,
            "model": result.asr_model, "revision": result.asr_revision,
            "task": "transcribe", "requested_language": "mr",
            "detected_language": result.detected_language,
            "selected_audio_sha256": result.audio_digest,
            "raw_transcript_sha256": result.raw_transcript_digest,
            "normalized_transcript_sha256": result.transcript_digest,
            "selected_start_ms": result.selected_start_ms,
            "selected_duration_ms": result.duration_ms}
        inference_config = {**inference_config,
            "reference_recipe_revision": result.recipe_revision,
            "sample_rate": result.sample_rate, "channels": result.channels}
        version.reference_asset_id = asset.id
        version.reference_transcript = result.transcript
        version.reference_transcript_digest = result.transcript_digest
        version.reference_audio_digest = result.audio_digest
        version.binding_digest = result.binding_digest
        version.model_manifest_json = model_manifest
        version.model_manifest_digest = canonical_digest(model_manifest)
        version.asr_manifest_json = asr_manifest
        version.asr_manifest_digest = canonical_digest(asr_manifest)
        version.inference_config_json = inference_config
        version.inference_config_digest = canonical_digest(inference_config)
        version.preparation_recipe_revision = PREPARATION_RECIPE
        version.status = "ready"
        version.ready_at = version.updated_at = now
        self._finish(job, now)
        for original in scope.assets.values():
            if original.version_id == version.id and original.kind == "original" and original.state != "purged":
                _schedule_asset_purge(db, scope, original, now)
        return version

    def publish_asset_purge(self, db, job_id, token, asset_id):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        asset = scope.assets.get(asset_id)
        if (job.kind != "purge" or job.request_key.split(":", 2)[1:2] != [asset_id]
                or asset is None or asset.version_id != version.id or asset.state != "purged"
                or asset.absent_since is None or asset.absence_checks < 1):
            raise StaleVoiceClaim()
        self._finish(job, now)
        return asset

    def schedule_asset_purge(self, db, *, legacy_id, asset_id, not_before=None):
        scope = lock_scope(db, legacy_id)
        asset = scope.assets.get(asset_id)
        if asset is None:
            return None
        return _schedule_asset_purge(db, scope, asset, utcnow(), not_before=not_before)

    def publish_synthesis(self, db, job_id, token, result: ClonedSpeech, authoritative_text: str):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        if (job.kind != "synthesize" or scope.legacy.deletion_requested_at is not None
                or scope.profile is None or scope.profile.status != "active"
                or scope.profile.current_version_id != version.id or version.status != "ready"):
            raise StaleVoiceClaim()
        validate_speech(result, authoritative_text, version.operation_generation)
        self._finish(job, now)
        return job

    def publish_purge(self, db, job_id, token):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        if job.kind != "purge" or version.status != "purge_pending" or any(
                asset.version_id == version.id and asset.state != "purged" for asset in scope.assets.values()):
            raise StaleVoiceClaim()
        version.status = "purged"
        version.reference_asset_id = None
        version.reference_transcript = None
        version.model_manifest_json = version.asr_manifest_json = version.inference_config_json = None
        version.updated_at = now
        self._finish(job, now)
        profile = scope.profile
        if profile and profile.status == "deleting" and all(
                item.status == "purged" for item in scope.versions.values() if item.voice_profile_id == profile.id):
            profile.status = "deleted"
            profile.deleted_at = profile.updated_at = now
        return version

    def heartbeat(self, db, job_id, token, *, now=None, lease_seconds=LEASE_SECONDS):
        now = now or utcnow()
        _, _, job = self._publication_scope(db, job_id, token, now)
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return job

    def fail(self, db, job_id, token, safe_code, *, retryable=True, now=None):
        if (not isinstance(safe_code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", safe_code)):
            raise ValueError("voice_safe_error_invalid")
        now = now or utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        job.safe_error_code = safe_code
        job.lease_token = job.lease_expires_at = job.writer_deadline = None
        if job.kind == "purge" or (retryable and job.attempts < 3):
            job.state = "retry_wait"
            job.next_attempt_at = now + timedelta(seconds=min(60, 2 ** job.attempts))
        else:
            job.state = "failed"
            job.finished_at = now
            if job.kind == "prepare" and scope.profile and scope.profile.desired_version_id == version.id:
                version.status = "failed"
                version.safe_failure_code = safe_code
                version.updated_at = now
                for asset in scope.assets.values():
                    if asset.version_id == version.id and asset.kind == "original" and asset.state != "purged":
                        _schedule_asset_purge(db, scope, asset, now,
                            not_before=asset.expires_at or now)
        return job


class VoiceProfileService:
    def status(self, db, owner_id, legacy_id):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        if profile is None:
            return {"exists": False, "lifecycle": None, "revision": 0, "language": "mr",
                "current_available": False, "candidate_available": False,
                "candidate_lifecycle": None, "candidate_version_id": None,
                "candidate_binding_digest": None, "failure_code": None}
        current = scope.versions.get(profile.current_version_id)
        candidate = scope.versions.get(profile.desired_version_id)
        return {"exists": True, "lifecycle": profile.status, "revision": profile.revision,
            "language": (current or candidate).language if current or candidate else "mr",
            "current_available": bool(profile.status == "active" and current and current.status == "ready"),
            "candidate_available": bool(candidate and candidate.status == "ready"),
            "candidate_lifecycle": candidate.status if candidate else None,
            "candidate_version_id": candidate.id if candidate else None,
            "candidate_binding_digest": candidate.binding_digest if candidate and candidate.status == "ready" else None,
            "failure_code": candidate.safe_failure_code if candidate and candidate.status == "failed" else None}

    def activate(self, db, owner_id, legacy_id, payload):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        if profile is None or profile.revision != payload.expected_revision or profile.status in {"deleting", "deleted"}:
            conflict()
        version = scope.versions.get(str(payload.version_id))
        consent = scope.consents.get(version.consent_receipt_id) if version else None
        asset = scope.assets.get(version.reference_asset_id) if version else None
        if (version is None or profile.desired_version_id != version.id or version.status != "ready"
                or version.binding_digest != payload.binding_digest or consent is None or consent.revoked_at is not None
                or consent.actor_user_id != owner_id or asset is None or asset.state != "available"
                or asset.sha256 != version.reference_audio_digest):
            conflict("voice_candidate_changed")
        now = utcnow()
        old = scope.versions.get(profile.current_version_id)
        profile.current_version_id = version.id
        profile.desired_version_id = None
        profile.status = "active"
        profile.revision += 1
        profile.updated_at = now
        version.activated_at = now
        if old and old.id != version.id:
            old.status = "superseded"
            _schedule_purge(db, scope, old, now)
        return profile

    def revoke(self, db, owner_id, legacy_id, expected_revision):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        if profile is None:
            raise HTTPException(404, detail="Voice Profile not found.")
        if profile.revision != expected_revision:
            conflict()
        if profile.status == "revoked":
            return profile
        if profile.status in {"deleting", "deleted"}:
            conflict("voice_deletion_in_progress")
        now = utcnow()
        profile.current_version_id = profile.desired_version_id = None
        profile.status = "revoked"
        profile.revoked_at = profile.updated_at = now
        profile.revision += 1
        for receipt in scope.consents.values():
            if receipt.voice_profile_id == profile.id:
                _revoke_consent(receipt, now)
        for version in scope.versions.values():
            if version.voice_profile_id == profile.id:
                _schedule_purge(db, scope, version, now)
        return profile

    def delete(self, db, owner_id, legacy_id, expected_revision):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        if profile is None:
            raise HTTPException(404, detail="Voice Profile not found.")
        if profile.revision != expected_revision:
            conflict()
        return self._delete_scope(db, scope)

    def _delete_scope(self, db, scope):
        profile = scope.profile
        if profile is None or profile.status in {"deleting", "deleted"}:
            return profile
        now = utcnow()
        profile.current_version_id = profile.desired_version_id = None
        profile.status = "deleting"
        profile.deletion_requested_at = profile.updated_at = now
        profile.revision += 1
        for receipt in scope.consents.values():
            if receipt.voice_profile_id == profile.id:
                _revoke_consent(receipt, now)
        for version in scope.versions.values():
            if version.voice_profile_id == profile.id:
                _schedule_purge(db, scope, version, now)
        if not any(v.voice_profile_id == profile.id for v in scope.versions.values()):
            profile.status = "deleted"
            profile.deleted_at = now
        return profile

    def request_legacy_purge(self, db, legacy: Legacy):
        scope = lock_scope(db, legacy.id, locked_legacy=legacy)
        return self._delete_scope(db, scope)


def request_account_voice_purge(db, owner_user_id: int):
    """Internal hook for a future account-deletion parent; owns no commit."""
    legacies = list(db.scalars(select(Legacy).where(Legacy.owner_user_id == owner_user_id)
        .order_by(Legacy.id).with_for_update()))
    service = VoiceProfileService()
    return [service.request_legacy_purge(db, legacy) for legacy in legacies]


@dataclass(frozen=True)
class AuthorizedSpeechContext:
    legacy_id: int
    mode: str
    actor_user_id: int
    turn_id: int | None
    authorization_generation: int


class LegacySpeechOrchestrator:
    """Accepts frozen answer text; deliberately has no brain/tool dependencies."""

    def resolve_version(self, db, context: AuthorizedSpeechContext):
        if context.mode == "rya":
            return None
        if context.mode != "legacy":
            raise ValueError("voice_mode_invalid")
        db.expire_all()
        legacy = db.get(Legacy, context.legacy_id)
        if legacy is None or legacy.deletion_requested_at is not None:
            return None
        profile = db.scalar(select(VoiceProfile).where(
            VoiceProfile.legacy_id == legacy.id, VoiceProfile.status == "active"))
        if profile is None or not profile.current_version_id:
            return None
        version = db.scalar(select(VoiceProfileVersion).where(
            VoiceProfileVersion.legacy_id == legacy.id,
            VoiceProfileVersion.voice_profile_id == profile.id,
            VoiceProfileVersion.id == profile.current_version_id,
            VoiceProfileVersion.status == "ready"))
        if version is None or not version.binding_digest:
            return None
        consent = db.scalar(select(VoiceConsentReceipt).where(
            VoiceConsentReceipt.legacy_id == legacy.id,
            VoiceConsentReceipt.voice_profile_id == profile.id,
            VoiceConsentReceipt.id == version.consent_receipt_id,
            VoiceConsentReceipt.revoked_at.is_(None)))
        return version if consent else None

    def admit_synthesis(self, db, context: AuthorizedSpeechContext, *, authoritative_text: str,
                        purpose: str, request_key: str):
        if not isinstance(authoritative_text, str) or not authoritative_text.strip() or len(authoritative_text) > 4096:
            raise ValueError("voice_authoritative_text_invalid")
        if purpose not in {"preview", "message", "live"}:
            raise ValueError("voice_purpose_invalid")
        version = self.resolve_version(db, context)
        if version is None:
            return None
        text_digest = hashlib.sha256(authoritative_text.encode("utf-8")).hexdigest()
        request_digest = canonical_digest({"legacy_id": context.legacy_id,
            "profile_id": version.voice_profile_id, "version_id": version.id,
            "text_digest": text_digest, "binding_digest": version.binding_digest,
            "model_digest": version.model_manifest_digest, "purpose": purpose})
        priority = 90 if purpose == "live" else 50 if purpose == "message" else 10
        return VoiceJobService().enqueue(db, legacy_id=context.legacy_id,
            profile_id=version.voice_profile_id, version_id=version.id, kind="synthesize",
            request_key=request_key, request_digest=request_digest, priority=priority,
            operation_generation=version.operation_generation)
