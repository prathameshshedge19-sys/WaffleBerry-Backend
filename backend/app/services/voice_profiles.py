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
    ClonedSpeech, PreparedReference, validate_prepared, validate_speech,
)

CONSENT_COPY = "l21-voice-consent-v1"
CONSENT_POLICY = "l21-voice-policy-v1"
PREPARATION_RECIPE = "l21-reference-contract-v1"
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


class VoiceEnrollmentService:
    """Reserve one enrollment attempt and one new human consent receipt."""

    def reserve_intent(self, db, owner_id: int, legacy_id: int, payload):
        scope = lock_scope(db, legacy_id, owner_id)
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
        if kind == "purge" and version.status != "purge_pending":
            job.state = "cancelled"
            job.finished_at = now
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

    def publish_prepared(self, db, job_id, token, result: PreparedReference, *, reference_asset_id,
                         model_manifest, asr_manifest, inference_config):
        now = utcnow()
        scope, version, job = self._publication_scope(db, job_id, token, now)
        if (job.kind != "prepare" or scope.legacy.deletion_requested_at is not None
                or scope.profile is None or scope.profile.status in {"revoked", "deleting", "deleted"}
                or scope.profile.desired_version_id != version.id or version.status != "preparing"):
            raise StaleVoiceClaim()
        validate_prepared(result, version.operation_generation)
        asset = scope.assets.get(reference_asset_id)
        if (asset is None or asset.version_id != version.id or asset.kind != "reference"
                or asset.state != "available" or asset.sha256 != result.audio_digest
                or asset.sample_rate != result.sample_rate or asset.channels != result.channels
                or asset.duration_ms != result.duration_ms):
            raise StaleVoiceClaim()
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
        return version

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
        return job


class VoiceProfileService:
    def status(self, db, owner_id, legacy_id):
        scope = lock_scope(db, legacy_id, owner_id)
        profile = scope.profile
        if profile is None:
            return {"exists": False, "lifecycle": None, "revision": 0, "language": "mr",
                "current_available": False, "candidate_available": False, "failure_code": None}
        current = scope.versions.get(profile.current_version_id)
        candidate = scope.versions.get(profile.desired_version_id)
        return {"exists": True, "lifecycle": profile.status, "revision": profile.revision,
            "language": (current or candidate).language if current or candidate else "mr",
            "current_available": bool(profile.status == "active" and current and current.status == "ready"),
            "candidate_available": bool(candidate and candidate.status == "ready"),
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
