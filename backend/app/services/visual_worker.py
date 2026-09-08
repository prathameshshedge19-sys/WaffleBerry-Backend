"""Durable L19 preparation and erasure, independent of web/realtime workers.

Public methods open/commit their own short transactions. Discovery never locks
jobs before their parents. PostgreSQL is required for multi-process execution;
SQLite is useful only for deterministic, sequential unit tests.

Providers may be explicitly injected for tests. The CLI has no fake-provider
fallback: real preparation remains disabled until the licensed, isolated local
adapter is available. Purge is usable with both visual feature flags disabled.

RELEASE BLOCKERS: S3 client exit is not a bound on remote PUT commit latency.
S3 sweeps therefore retain purge_pending registrations and retry indefinitely;
they never report final erasure. A storage-level commit bound or a reviewed
durable post-purge audit is required before remote-erasure release acceptance.
Original S3 reads also remain disabled here until the source adapter provides
a process-bounded 20 MiB read. A byte-count limit alone is not a time limit.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import logging
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import case, or_, select, text

from app.database import SessionLocal
from app.models.visual_companion import (
    VisualCompanionAsset as Asset, VisualCompanionVersion as Version,
    VisualGenerationJob as Job,
)
from app.services.media_storage import StorageError, get_source_storage
from app.services.visual_companions import (
    CONFIRMATION, RECIPE, ROLES, aware, digest, enqueue_purge, lock_scope, utcnow,
)
from app.services.visual_companions import require_source
from app.services.visual_storage import VisualStorage

LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 10
HARD_DEADLINE_SECONDS = 120
MAX_PREPARE_ATTEMPTS = 2
CLAIM_COORDINATOR = 0x4C31395649535541
ACTIVE_STATES = ("queued", "running", "retry_wait")


class StaleClaim(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    id: str
    role: str
    key: str
    sha256: str
    byte_size: int
    mime_type: str
    writer_deadline: object
    object_version: str | None = None


class VisualWorker:
    def __init__(self, sessions=SessionLocal, storage=None, *, provider=None,
                 source_storage=None, clock=utcnow, heartbeat_seconds=HEARTBEAT_SECONDS):
        self.sessions = sessions
        self.source_storage = source_storage
        self.storage = storage if storage is not None else VisualStorage(get_source_storage())
        self.provider = provider
        self.clock = clock
        if not 0 < heartbeat_seconds <= HEARTBEAT_SECONDS:
            raise ValueError("heartbeat_seconds must be in (0, 10]")
        self.heartbeat_seconds = heartbeat_seconds

    def _now(self):
        return aware(self.clock())

    @staticmethod
    def _jobs(db, version_id):
        return list(db.scalars(select(Job).where(Job.version_id == version_id)
            .order_by(Job.id).execution_options(populate_existing=True).with_for_update()))

    def _scope(self, db, job_id):
        # Read scalar identity only, then follow the common parent lock order.
        identity = db.execute(select(Job.legacy_id, Job.version_id).where(Job.id == job_id)).first()
        if identity is None:
            raise StaleClaim()
        legacy, profile, versions, sources = lock_scope(db, identity.legacy_id)
        version = versions.get(identity.version_id)
        if profile is None or version is None or version.companion_id != profile.id:
            raise StaleClaim()
        jobs = self._jobs(db, version.id)
        job = next((j for j in jobs if j.id == job_id), None)
        if job is None or job.companion_id != profile.id:
            raise StaleClaim()
        return legacy, profile, version, sources, job

    @staticmethod
    def _assets(db, version):
        return list(db.scalars(select(Asset).where(
            Asset.legacy_id == version.legacy_id, Asset.companion_id == version.companion_id,
            Asset.version_id == version.id).order_by(Asset.id)
            .execution_options(populate_existing=True).with_for_update()))

    def _leased(self, job, token, now):
        return (job.state == "running" and job.lease_token == token
                and job.lease_expires_at is not None and aware(job.lease_expires_at) > now)

    def _eligible(self, db, scope, now):
        legacy, profile, version, sources, _ = scope
        if (legacy.setup_status not in {"active", "collecting_identity"}
                or legacy.deletion_requested_at is not None or profile.deleted_at is not None
                or profile.desired_version_id != version.id
                or version.state not in {"queued", "preparing"}
                or version.removed_at is not None
                or version.confirmed_by_user_id != legacy.owner_user_id
                or version.confirmation_copy_version != CONFIRMATION
                or not version.crop_json or digest(version.crop_json) != version.crop_digest
                or version.recipe_version != RECIPE
                or (version.expires_at is not None and aware(version.expires_at) <= now)):
            return False
        try:
            require_source(version, sources, db)
        except HTTPException:
            return False
        return True

    def _prepare_fence(self, db, scope, token, now):
        job = scope[-1]
        return (job.kind == "prepare" and self._leased(job, token, now)
                and job.writer_deadline is not None and aware(job.writer_deadline) > now
                and self._eligible(db, scope, now))

    @staticmethod
    def _finish(job, now, state="succeeded", code=None):
        job.state, job.finished_at, job.last_error_code = state, now, code
        job.lease_token = job.lease_expires_at = None
        job.next_attempt_at = None

    def _arm_cleanup(self, db, version, now):
        """Asset-only cleanup must not cancel a newer attempt or ready bundle."""
        db.flush()  # populate_existing must not discard our pending job changes.
        jobs = self._jobs(db, version.id)
        purge = next((j for j in jobs if j.kind == "purge"), None)
        if purge is None:
            db.add(Job(id=str(uuid4()), legacy_id=version.legacy_id,
                companion_id=version.companion_id, version_id=version.id,
                kind="purge", state="queued", attempts=0, next_attempt_at=now))
        elif purge.state not in ACTIVE_STATES:
            purge.state, purge.next_attempt_at, purge.finished_at = "queued", now, None
            purge.lease_token = purge.lease_expires_at = None

    def _retire(self, db, profile, version, now):
        # Current and enabled are owner decisions; this worker never activates.
        if profile.desired_version_id == version.id:
            profile.desired_version_id = None
            profile.revision += 1
            profile.updated_at = now
        enqueue_purge(db, profile, version, now)

    def claim(self):
        """Return (job_id, fresh UUID token), or None; globally serialize prep."""
        with self.sessions.begin() as db:
            if db.bind.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_COORDINATOR})
            now = self._now()
            expired = db.execute(select(Version.legacy_id, Version.id).where(
                Version.approved_at.is_(None), Version.expires_at <= now,
                Version.state.in_(("queued", "preparing", "ready", "failed", "cancelled")))
                .order_by(Version.id).limit(32)).all()
            for legacy_id, version_id in expired:
                _, profile, versions, _ = lock_scope(db, legacy_id)
                version = versions.get(version_id)
                if (profile is not None and version is not None and version.approved_at is None
                        and version.expires_at and aware(version.expires_at) <= now
                        and version.state not in {"purge_pending", "purged"}):
                    self._retire(db, profile, version, now)
                    db.flush()
            # Discovery is intentionally unlocked. Parents and jobs are then
            # locked and every eligibility condition is evaluated again.
            candidates = db.scalars(select(Job.id).where(
                Job.state.in_(ACTIVE_STATES),
                or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
                or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
                or_(Job.kind == "purge", self.provider is not None),
            ).order_by(case((Job.kind == "purge", 0), else_=1), Job.created_at, Job.id).limit(64)).all()
            for job_id in candidates:
                scope = self._scope(db, job_id)
                _, profile, version, _, job = scope
                now = self._now()
                if (job.state not in ACTIVE_STATES
                        or (job.next_attempt_at and aware(job.next_attempt_at) > now)
                        or (job.lease_expires_at and aware(job.lease_expires_at) > now)):
                    continue
                if job.kind == "prepare":
                    # Cancellation/lease expiry does not prove the old local
                    # process or dispatched PUT has exited. Keep its deadline.
                    busy = db.scalar(select(Job.id).where(Job.kind == "prepare", or_(
                        Job.writer_deadline > now,
                        (Job.state == "running") & (Job.lease_expires_at > now))).limit(1))
                    writing = db.scalar(select(Asset.id).where(
                        Asset.state.in_(("reserved", "purge_pending")), Asset.writer_deadline > now).limit(1))
                    if busy or writing:
                        continue
                    if not self._eligible(db, scope, now):
                        self._retire(db, profile, version, now)
                        db.flush()
                        continue
                    if job.attempts >= MAX_PREPARE_ATTEMPTS:
                        self._finish(job, now, "failed", "visual_attempts_exhausted")
                        version.state, version.failure_code = "failed", "visual_attempts_exhausted"
                        self._arm_cleanup(db, version, now)
                        for asset in self._assets(db, version):
                            if asset.state == "reserved":
                                asset.state = "purge_pending"
                        db.flush()
                        continue
                    # Crash recovery: a new attempt never reuses old keys.
                    self._arm_if_reserved(db, version, now)
                    version.state = "preparing"
                    job.writer_deadline = now + timedelta(seconds=HARD_DEADLINE_SECONDS)
                token = str(uuid4())
                job.state, job.lease_token = "running", token
                job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                job.attempts += 1
                job.started_at, job.finished_at = now, None
                job.next_attempt_at, job.last_error_code = None, None
                return job.id, token
        return None

    def _arm_if_reserved(self, db, version, now):
        # Check without acquiring assets; job locks always precede asset locks.
        if db.scalar(select(Asset.id).where(Asset.version_id == version.id,
                                           Asset.state == "reserved").limit(1)):
            self._arm_cleanup(db, version, now)
            for asset in self._assets(db, version):
                if asset.state == "reserved":
                    asset.state = "purge_pending"

    def heartbeat(self, job_id, token):
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            now, job = self._now(), scope[-1]
            if not self._leased(job, token, now):
                return False
            if job.kind == "prepare" and not self._prepare_fence(db, scope, token, now):
                return False
            deadline = now + timedelta(seconds=LEASE_SECONDS)
            job.lease_expires_at = min(deadline, aware(job.writer_deadline)) if job.kind == "prepare" else deadline
            return True

    @contextmanager
    def _heartbeat(self, job_id, token):
        stopped = threading.Event()

        def renew():
            while not stopped.wait(self.heartbeat_seconds):
                try:
                    if not self.heartbeat(job_id, token):
                        return
                except Exception:
                    return  # Fail closed at the next mandatory publication fence.

        thread = threading.Thread(target=renew, name="visual-lease", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    @staticmethod
    def _specs(bundle):
        from app.services.visual_provider import validate_bundle
        specs = validate_bundle(bundle)
        if len(specs) != 3 or {s["logical_role"] for s in specs} != ROLES:
            raise ValueError("visual_bundle_invalid")
        return specs

    def _storage_matches(self, asset):
        return (asset.storage_backend == self.storage.backend_name
            and asset.encryption_key_id == self.storage.encryption_key_id
            and (asset.storage_backend != 's3' or (asset.storage_bucket
                and asset.storage_bucket == self.storage.bucket_name)))

    def _reservation(self, asset):
        if not self._storage_matches(asset):
            raise StorageError('visual_storage_scope_mismatch')
        return Reservation(asset.id, asset.logical_role, asset.object_key, asset.sha256,
            asset.byte_size, asset.mime_type, aware(asset.writer_deadline), asset.object_version)

    def reserve_assets(self, job_id, token, bundle):
        """Validate, then commit all three keys before the first external PUT."""
        specs = self._specs(bundle)
        identity_hash = json.loads(bundle.assets["rig"])["request_identity_sha256"]
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            version, now = scope[2], self._now()
            if not self._prepare_fence(db, scope, token, now):
                raise StaleClaim()
            if identity_hash != digest(self._identity(version)):
                raise ValueError("visual_bundle_identity_mismatch")
            if db.scalar(select(Asset.id).where(Asset.version_id == version.id, Asset.attempt_id == token).limit(1)):
                raise StaleClaim()  # Never redispatch a possibly completed key.
            rows = []
            for spec in specs:
                data = bundle.assets[spec["logical_role"]]
                asset = Asset(id=str(uuid4()), legacy_id=version.legacy_id,
                    companion_id=version.companion_id, version_id=version.id,
                    attempt_id=token, logical_role=spec["logical_role"],
                    storage_backend=self.storage.backend_name,
                    storage_bucket=getattr(self.storage, 'bucket_name', None),
                    encryption_key_id=self.storage.encryption_key_id,
                    object_key=f"visual/{version.id}/{token}/{uuid4()}", state="reserved",
                    sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data),
                    mime_type=spec["mime_type"], width=spec.get("width"), height=spec.get("height"),
                    writer_deadline=scope[-1].writer_deadline, created_at=now)
                db.add(asset)
                rows.append(self._reservation(asset))
            db.flush()
            return tuple(rows)

    def _attempt(self, job_id, token):
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            return tuple(self._reservation(a) for a in self._assets(db, scope[2]) if a.attempt_id == token)

    def _record_put(self, job_id, token, asset_id, stored):
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            # Metadata is safe to record after lease loss. Do not alter a newer
            # job lease or expose the bytes through an available state.
            for asset in self._assets(db, scope[2]):
                if asset.id == asset_id and asset.attempt_id == token:
                    asset.object_version = stored.version
                    asset.write_state = 'confirmed'
                    asset.write_confirmed_at = self._now()
                    return
            raise StaleClaim()

    def begin_put(self, job_id, token, asset_id):
        """Durable single-dispatch fence. A crash afterward is uncertain."""
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            now = self._now()
            if not self._prepare_fence(db, scope, token, now):
                raise StaleClaim()
            for asset in self._assets(db, scope[2]):
                if asset.id == asset_id and asset.attempt_id == token:
                    if not self._storage_matches(asset):
                        raise StorageError('visual_storage_scope_mismatch')
                    if asset.write_state != 'reserved' or (aware(asset.writer_deadline)-now).total_seconds() <= 32:
                        raise StaleClaim()
                    asset.write_state = 'dispatching'
                    return
            raise StaleClaim()

    def cleanup_attempt(self, job_id, token):
        """Durable stale-output cleanup independent of the current job lease."""
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            version, now = scope[2], self._now()
            self._arm_cleanup(db, version, now)
            for asset in self._assets(db, version):
                if asset.attempt_id == token and asset.state != "available":
                    # Re-arm even an earlier 'purged' row after a late PUT.
                    asset.state, asset.purged_at = "purge_pending", None

    def publish(self, job_id, token, bundle):
        """Verify exact registered objects outside SQL, publish ready atomically."""
        self._specs(bundle)
        identity_hash = json.loads(bundle.assets["rig"])["request_identity_sha256"]
        reservations = self._attempt(job_id, token)
        if len(reservations) != 3 or {a.role for a in reservations} != ROLES:
            raise ValueError("visual_bundle_incomplete")
        for asset in reservations:
            data = bundle.assets[asset.role]
            if asset.sha256 != hashlib.sha256(data).hexdigest() or asset.byte_size != len(data):
                raise ValueError("visual_bundle_changed")
            if self.storage.verify(asset.key, asset.sha256, asset.byte_size, version=asset.object_version) is not True:
                raise StorageError("visual_verification_failed")
        # Digest binds the recipe and content, never keys or browser input.
        bundle_digest = digest({"recipe": RECIPE, "assets": [
            {"role": a.role, "sha256": a.sha256, "byte_size": a.byte_size, "mime_type": a.mime_type}
            for a in sorted(reservations, key=lambda a: a.role)]})
        stale = False
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            now, version, job = self._now(), scope[2], scope[-1]
            assets = self._assets(db, version)
            selected = [a for a in assets if a.attempt_id == token]
            if (not self._prepare_fence(db, scope, token, now)
                    or identity_hash != digest(self._identity(version))
                    or len(selected) != 3 or any(a.state != "reserved" for a in selected)
                    or any(a.state == "available" for a in assets)
                    or any(not self._storage_matches(a) for a in selected)):
                stale = True
            else:
                for asset in selected:
                    asset.state = "available"
                version.state, version.bundle_digest = "ready", bundle_digest
                version.completed_at, version.failure_code = now, None
                version.expires_at = version.expires_at or now + timedelta(days=7)
                self._finish(job, now)
                job.writer_deadline = None
        if stale:
            self.cleanup_attempt(job_id, token)
            return "stale"
        return "ready"

    def _failed(self, job_id, token, *, transient=False, code="visual_preparation_failed"):
        self.cleanup_attempt(job_id, token)
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            job, version, now = scope[-1], scope[2], self._now()
            if job.lease_token != token or job.state != "running":
                return "stale"
            if not self._eligible(db, scope, now):
                self._retire(db, scope[1], version, now)
                return "cancelled"
            job.writer_deadline = None  # Local execution has returned.
            if transient and job.attempts < MAX_PREPARE_ATTEMPTS:
                job.state, job.last_error_code = "retry_wait", code
                job.next_attempt_at = now + timedelta(seconds=30)
                job.lease_token = job.lease_expires_at = None
                version.state = "queued"
                return "retry_wait"
            self._finish(job, now, "failed", code)
            version.state, version.failure_code, version.completed_at = "failed", code, now
            return "failed"

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return "idle"
        job_id, token = claim
        with self.sessions() as db:
            kind = db.scalar(select(Job.kind).where(Job.id == job_id))
        with self._heartbeat(job_id, token):
            if kind == "purge":
                return self.purge(job_id, token)
            try:
                with self.sessions.begin() as db:
                    scope = self._scope(db, job_id)
                    if not self._prepare_fence(db, scope, token, self._now()):
                        raise StaleClaim()
                    version = scope[2]
                    original = require_source(version, scope[3], db)
                    key, expected_hash = original.object_key, version.source_sha256
                    original_backend, original_key_id = original.storage_backend, original.encryption_key_id
                    original_version, original_size = original.object_version, original.byte_size
                    crop, identity = dict(version.crop_json), self._identity(version)
                    provider_name, model_digest = version.provider_name, version.model_digest
                    prepare_deadline = aware(scope[-1].writer_deadline)
                if (getattr(self.provider, "provider_name", None) != provider_name
                        or getattr(self.provider, "model_digest", None) != model_digest):
                    return self._failed(job_id, token, code="visual_provider_mismatch")
                source_storage = self.source_storage or get_source_storage()
                if (source_storage.backend_name != original_backend
                        or source_storage.encryption_key_id != original_key_id):
                    raise ValueError("visual_source_storage_changed")
                data = VisualStorage(source_storage).read_original(key, original_version)
                if len(data) != original_size or len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_hash:
                    raise ValueError("visual_source_changed")
                if not self.heartbeat(job_id, token):
                    raise StaleClaim()
                from app.services.visual_local_provider import LocalPortraitRigProvider
                if isinstance(self.provider, LocalPortraitRigProvider):
                    last_check = [0.]
                    def cancelled():
                        if time.monotonic()-last_check[0] < .5:
                            return False
                        last_check[0] = time.monotonic()
                        return not self.heartbeat(job_id, token)
                    bundle = self.provider.prepare(data, crop, identity,
                        cancelled=cancelled,
                        deadline=time.monotonic() + max(0, (prepare_deadline-self._now()).total_seconds()))
                else:
                    bundle = self.provider.prepare(data, crop, identity)
                reservations = self.reserve_assets(job_id, token, bundle)
                for asset in reservations:
                    if not self.heartbeat(job_id, token):
                        raise StaleClaim()
                    # Leave the storage subprocess enough time to terminate
                    # within the durable writer deadline (30s + shutdown).
                    if (asset.writer_deadline - self._now()).total_seconds() <= 32:
                        raise StaleClaim()
                    self.begin_put(job_id, token, asset.id)
                    stored = self.storage.put(asset.key, bundle.assets[asset.role], asset.mime_type)
                    self._record_put(job_id, token, asset.id, stored)
                return self.publish(job_id, token, bundle)
            except StaleClaim:
                return self._failed(job_id, token, code="visual_deadline_or_lease_lost")
            except StorageError:
                return self._failed(job_id, token, transient=True, code="visual_storage_unavailable")
            except Exception as exc:
                # Never persist/print exception strings or provider diagnostics.
                code = getattr(exc, 'code', None)
                allowed = {'visual_needs_recrop','visual_crop_not_square','visual_native_timeout',
                           'visual_native_cancelled','visual_native_isolation_unavailable','visual_model_mismatch'}
                return self._failed(job_id, token, code=code if code in allowed else 'visual_preparation_failed')

    @staticmethod
    def _identity(version):
        return {"source_sha256": version.source_sha256, "request_digest": version.request_digest,
            "recipe_version": version.recipe_version, "model_digest": version.model_digest,
            "source_generation": version.source_generation,
            "source_artifact_generation": version.source_artifact_generation}

    def _purge_retry(self, job_id, token, *, waiting=False, remote_unproven=False):
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            job, now = scope[-1], self._now()
            if not self._leased(job, token, now):
                return "stale"
            job.state = "retry_wait"
            job.next_attempt_at = now + timedelta(seconds=10 if waiting else min(300, 10 * 2 ** min(job.attempts, 5)))
            job.last_error_code = ("visual_remote_erasure_unproven" if remote_unproven else
                "visual_writer_pending" if waiting else "visual_erasure_unconfirmed")
            job.lease_token = job.lease_expires_at = None
            logging.getLogger('visual_purge').error(json.dumps({
                'event':'visual_purge_pending','job_id':job.id,'code':job.last_error_code,
                'attempts':job.attempts}))
            return "retry_wait"

    def purge(self, job_id, token):
        """Sweep exact keys, all object versions, only after registered writers."""
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            job, version, now = scope[-1], scope[2], self._now()
            if job.kind != "purge" or not self._leased(job, token, now):
                return "stale"
            assets = self._assets(db, version)
            whole_version = version.state in {"purge_pending", "purged"}
            targets = [a for a in assets if a.state != "purged" and (whole_version or a.state == "purge_pending")]
            waiting = any(aware(a.writer_deadline) > now for a in targets)
            # A cancelled provider can still be running before it reserved rows.
            writer = db.scalar(select(Job.writer_deadline).where(Job.version_id == version.id, Job.kind == "prepare"))
            waiting = waiting or (whole_version and writer is not None and aware(writer) > now)
            keys = {a.id: a.object_key for a in targets}
            wrong_storage = any(not self._storage_matches(a) for a in targets)
        if wrong_storage:
            return self._purge_retry(job_id, token)
        if waiting:
            return self._purge_retry(job_id, token, waiting=True)
        if self.storage.backend_name == 's3':
            try:
                if not self._remote_erasure(job_id, token, keys):
                    return self._purge_retry(job_id, token, remote_unproven=True)
            except Exception:
                return self._purge_retry(job_id, token)
        try:
            for key in keys.values():
                # erase() confirms absence of the exact key and all its versions;
                # errors/uncertain absence must raise, not become a success.
                if self.storage.erase(key) is False:
                    raise StorageError("visual_erasure_unconfirmed")
        except Exception:
            return self._purge_retry(job_id, token)
        with self.sessions.begin() as db:
            scope = self._scope(db, job_id)
            job, version, now = scope[-1], scope[2], self._now()
            if not self._leased(job, token, now):
                return "stale"
            assets = self._assets(db, version)
            for asset in assets:
                if asset.id in keys and asset.object_key == keys[asset.id] and asset.state != "available":
                    asset.state, asset.purged_at = "purged", now
            db.flush()
            remaining = [a for a in assets if a.state != "purged" and (
                version.state in {"purge_pending", "purged"} or a.state == "purge_pending")]
            if remaining:
                job.state, job.next_attempt_at = "retry_wait", now + timedelta(seconds=10)
                job.lease_token = job.lease_expires_at = None
                return "retry_wait"
            if version.state == "purge_pending":
                version.state, version.removed_at, version.crop_json = "purged", now, None
            self._finish(job, now)
            return "purged"

    def _remote_erasure(self, job_id, token, keys):
        """Positive completion proof, then two absent sweeps >=60s apart.

        Reserved means never dispatched; confirmed means the sole PUT returned
        or exact expected bytes were observed. Dispatching+absent is ambiguous
        forever until storage/operator evidence resolves it; time cannot erase
        that uncertainty. No stability timer alone finalizes an unknown writer.
        """
        complete = True
        for asset_id, key in keys.items():
            with self.sessions.begin() as db:
                scope = self._scope(db, job_id)
                if not self._leased(scope[-1], token, self._now()):
                    raise StaleClaim()
                asset = next(a for a in self._assets(db, scope[2]) if a.id == asset_id)
                if (asset.storage_backend != 's3' or not asset.storage_bucket
                        or asset.storage_bucket != getattr(self.storage, 'bucket_name', None)
                        or asset.encryption_key_id != self.storage.encryption_key_id):
                    raise StorageError('visual_storage_scope_mismatch')
                uncertain = asset.write_state == 'dispatching'
                checksum, size = asset.sha256, asset.byte_size
            if uncertain:
                if self.storage.reconcile_write(key, checksum, size) is not True:
                    complete = False
                    continue
                # Record proof BEFORE deletion. Crash cannot forget a known
                # committed object and mistakenly turn it into unknown absence.
                with self.sessions.begin() as db:
                    scope = self._scope(db, job_id)
                    asset = next(a for a in self._assets(db, scope[2]) if a.id == asset_id)
                    asset.write_state, asset.write_confirmed_at = 'confirmed', self._now()
            self.storage.erase(key)
            with self.sessions.begin() as db:
                scope = self._scope(db, job_id)
                now = self._now()
                if not self._leased(scope[-1], token, now):
                    raise StaleClaim()
                asset = next(a for a in self._assets(db, scope[2]) if a.id == asset_id)
                if asset.absent_since is None:
                    asset.absent_since = now
                asset.absence_checks += 1
                if asset.absence_checks < 2 or (now-aware(asset.absent_since)).total_seconds() < 60:
                    complete = False
        return complete


def main():
    import argparse
    import json
    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Process private visual preparation and durable erasure")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health", action="store_true", help="Restricted operator JSON summary; no portrait metadata")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 60:
        parser.error("poll-seconds must be between 1 and 60")
    if args.health:
        from app.services.visual_health import health_summary
        print(json.dumps(health_summary(SessionLocal)), flush=True)
        return
    import sys
    if sys.platform == 'linux':
        from app.services.visual_local_provider import sweep_native_workspaces
        try:
            sweep_native_workspaces()
        except Exception:
            logging.getLogger('visual_purge').error(json.dumps({
                'event':'visual_native_cleanup_pending','code':'visual_native_isolation_unavailable'}))
    provider = None
    if get_settings().visual_preparation_enabled:
        from app.services.visual_local_provider import configured_provider
        try:
            provider = configured_provider()
        except Exception:
            print(json.dumps({"event": "visual_preparation_disabled", "code": "visual_provider_unavailable"}), flush=True)
    worker = VisualWorker(provider=provider)
    while True:
        try:
            outcome = worker.run_once()
        except Exception:
            outcome = "worker_unavailable"
        print(json.dumps({"event": "visual_worker_cycle", "outcome": outcome}), flush=True)
        if args.once:
            return
        if outcome != "ready":
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
