"""Narrow voice facade over L19's exact-key, all-version erasure proof."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.voice_profile import VoiceAsset
from app.services.media_storage import get_source_storage
from app.services.visual_storage import VisualStorage
from app.services.voice_observability import emit as voice_event


def utcnow():
    return datetime.now(timezone.utc)


class VoiceStorage:
    def __init__(self, storage=None):
        self.storage = storage if isinstance(storage, VisualStorage) else VisualStorage(storage or get_source_storage())
        self.backend_name = self.storage.backend_name
        self.bucket_name = self.storage.bucket_name
        self.encryption_key_id = self.storage.encryption_key_id

    def put_original(self, key: str, data: bytes, mime: str):
        return self.storage.put_original(key, data, mime)

    def put_reference(self, key: str, data: bytes):
        return self.storage.put(key, data, "audio/wav")

    def put_generated(self, key: str, data: bytes):
        return self.storage.put(key, data, "audio/wav")

    def read_original(self, asset: VoiceAsset) -> bytes:
        return self.storage.read_original(asset.object_key, asset.object_version)

    def read_private(self, asset: VoiceAsset) -> bytes:
        return self.storage.read(asset.object_key, asset.object_version)

    def verify(self, asset: VoiceAsset) -> bool:
        verify = self.storage.verify_original if asset.kind == "original" else self.storage.verify
        return verify(asset.object_key, asset.sha256, asset.byte_count, asset.object_version)

    def erase_registered(self, sessions, asset_id: str) -> bool:
        """Erase one exact registered key, then persist positive absence proof.

        No database lock spans storage I/O. A changed registration fails closed;
        any storage uncertainty raises and leaves the durable row purge_pending.
        """
        with sessions() as db:
            asset = db.get(VoiceAsset, asset_id)
            if asset is None or asset.state == "purged":
                return bool(asset)
            identity = (asset.legacy_id, asset.voice_profile_id, asset.version_id,
                asset.object_key, asset.object_version, asset.storage_backend,
                asset.storage_bucket, asset.encryption_key_id)
            if (asset.state != "purge_pending"
                    or not asset.object_key.startswith(f"legarya/legacies/{asset.legacy_id}/voice/")):
                return False
            if (self.storage.backend_name != asset.storage_backend
                    or self.storage.encryption_key_id != asset.encryption_key_id
                    or (asset.storage_backend == "s3" and self.storage.bucket_name != asset.storage_bucket)):
                return False
        self.storage.erase(identity[3])
        with sessions.begin() as db:
            asset = db.scalar(select(VoiceAsset).where(VoiceAsset.id == asset_id).with_for_update())
            if asset is None:
                return False
            current = (asset.legacy_id, asset.voice_profile_id, asset.version_id,
                asset.object_key, asset.object_version, asset.storage_backend,
                asset.storage_bucket, asset.encryption_key_id)
            if current != identity or asset.state != "purge_pending":
                return False
            now = utcnow()
            asset.absent_since = now
            asset.absence_checks += 1
            asset.state = "purged"
            asset.purged_at = now
            voice_event("purge_completed")
            return True
