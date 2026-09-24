"""Crash-safe S3 multipart writes and cancellation, never absence-as-write-proof.

The cancellation handle is committed before the first private byte is sent.
An ambiguous initiation can leave only an EMPTY upload. A closing tombstone
prevents the late initiator from sending parts. Tombstones are not cascaded with
product rows; the sweeper continues cancelling late empty uploads after erasure.
No SQL transaction spans remote I/O. Keys are immutable and never retried.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.models.storage_write import StorageWrite
from app.services.media_storage import StorageError, StoredObject

PART_BYTES = 5 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DurableWrites:
    def __init__(self, storage, sessions, *, clock=utcnow):
        self.storage, self.sessions, self.clock = storage, sessions, clock
        endpoint = getattr(getattr(getattr(storage.storage, "client", None), "meta", None), "endpoint_url", "injected-test")
        self.scope = hashlib.sha256(json.dumps([endpoint, storage.bucket_name,
            storage.encryption_key_id], separators=(",", ":")).encode()).hexdigest()

    def identity(self, key):
        return hashlib.sha256((self.scope + "\n" + key).encode()).hexdigest()

    def known(self, key):
        with self.sessions() as db:
            return db.get(StorageWrite, self.identity(key)) is not None

    def reserve(self, db, key):
        db.add(StorageWrite(id=self.identity(key), scope=self.scope, object_key=key,
            state="reserved", writer_until=self.clock()))

    def _admit(self, key, *, upload_id=None):
        with self.sessions.begin() as db:
            row = db.scalar(select(StorageWrite).where(StorageWrite.id == self.identity(key)).with_for_update())
            if row is None or row.state != "writing":
                raise StorageError("storage_write_cancelled")
            # Admission gives the next isolated RPC a bounded execution window.
            # Closing preserves this deadline, drains the caller, THEN aborts.
            row.writer_until = self.clock() + timedelta(seconds=self.storage.max_operation_seconds + 5)
            if upload_id is not None:
                row.upload_id = upload_id

    def put(self, key, data, mime):
        try:
            with self.sessions.begin() as db:
                row = db.scalar(select(StorageWrite).where(StorageWrite.id == self.identity(key)).with_for_update())
                if row is None:
                    row = StorageWrite(id=self.identity(key), scope=self.scope, object_key=key)
                    db.add(row)
                elif row.state != "reserved":
                    raise StorageError("storage_write_already_registered")
                row.state = "writing"
                row.writer_until = self.clock() + timedelta(seconds=self.storage.max_operation_seconds + 5)
        except IntegrityError:
            raise StorageError("storage_write_already_registered") from None
        upload_id = self.storage._raw_run("multipart_begin", key, mime)
        # If deletion won the race, no content has been transmitted. The
        # tombstone sweeper aborts this (possibly late) empty initiation.
        self._admit(key, upload_id=upload_id)
        parts = []
        for offset in range(0, len(data), PART_BYTES):
            self._admit(key)
            number = len(parts) + 1
            etag = self.storage._raw_run("multipart_part", key,
                data[offset:offset + PART_BYTES], upload_id, number)
            parts.append({"PartNumber": number, "ETag": etag})
        self._admit(key)
        result = self.storage._raw_run("multipart_complete", key, upload_id, parts)
        with self.sessions.begin() as db:
            row = db.scalar(select(StorageWrite).where(StorageWrite.id == self.identity(key)).with_for_update())
            if row.state != "writing":
                raise StorageError("storage_write_cancelled")
            row.state = "confirmed"
            row.writer_until = self.clock()
        return StoredObject(len(data), result.get("ETag"), result.get("VersionId"))

    def erase(self, key):
        with self.sessions.begin() as db:
            if db.scalar(select(StorageWrite.id).where(StorageWrite.object_key == key,
                    StorageWrite.scope != self.scope).limit(1)) is not None:
                raise StorageError("storage_scope_changed")
            row = db.scalar(select(StorageWrite).where(StorageWrite.id == self.identity(key)).with_for_update())
            if row is None:
                # Only callers with pre-existing terminal/never-dispatched
                # proof may reach this path for pre-cutover objects.
                row = StorageWrite(id=self.identity(key), scope=self.scope, object_key=key,
                    state="closing", writer_until=self.clock())
                db.add(row)
            else:
                row.state = "closing"
            deadline, upload_id = aware(row.writer_until), row.upload_id
            row.reconcile_after = max(deadline, self.clock() + timedelta(seconds=30))
        if deadline > self.clock():
            raise StorageError("storage_writer_draining")
        self.storage._raw_run("multipart_cancel", key, upload_id)
        self.storage._raw_run("erase", key)
        with self.sessions.begin() as db:
            row = db.scalar(select(StorageWrite).where(StorageWrite.id == self.identity(key)).with_for_update())
            row.state, row.erased_at = "erased", self.clock()
            row.reconcile_after = self.clock() + timedelta(hours=1)

    def sweep(self, limit=16):
        """Restart-safe follow-up for closing and late-empty-init obligations."""
        with self.sessions() as db:
            keys = list(db.scalars(select(StorageWrite.object_key).where(
                StorageWrite.scope == self.scope, StorageWrite.state.in_(["closing", "erased"]),
                or_(StorageWrite.reconcile_after.is_(None), StorageWrite.reconcile_after <= self.clock()),
                StorageWrite.writer_until <= self.clock()).order_by(
                    StorageWrite.reconcile_after.asc().nullsfirst(), StorageWrite.id).limit(limit)))
        for key in keys:
            try:
                self.erase(key)
            except StorageError:
                with self.sessions.begin() as db:
                    row = db.get(StorageWrite, self.identity(key))
                    row.reconcile_after = self.clock() + timedelta(seconds=30)
                continue
        return len(keys)
