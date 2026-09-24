"""Offline adversarial multipart tests; no real credentials or object storage."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.storage_write import StorageWrite
from app.services.media_storage import StorageError
from app.services.visual_storage import VisualStorage
from tests.test_media_sources_l16 import media_db, _reserve
from tests.test_visual_storage_l19 import FakeS3, error


class MultipartS3(FakeS3):
    def __init__(self):
        super().__init__()
        self.uploads, self.sequence = {}, 0
        self.lost = set()
        self.false_abort = False
        self.on_begin = None

    def create_multipart_upload(self, **kw):
        self._call("begin", kw)
        self.sequence += 1
        identifier = "upload-" + str(self.sequence)
        self.uploads[identifier] = (kw["Key"], {})
        if self.on_begin:
            self.on_begin()
        if "begin" in self.lost:
            raise TimeoutError()
        return {"UploadId": identifier}

    def upload_part(self, **kw):
        self._call("part", kw)
        if kw["UploadId"] not in self.uploads:
            raise error("NoSuchUpload", 404)
        self.uploads[kw["UploadId"]][1][kw["PartNumber"]] = kw["Body"]
        if "part" in self.lost:
            raise TimeoutError()
        return {"ETag": "part-etag"}

    def complete_multipart_upload(self, **kw):
        self._call("complete", kw)
        if "complete-before" in self.lost:
            raise TimeoutError()
        if kw["UploadId"] not in self.uploads:
            raise error("NoSuchUpload", 404)
        key, parts = self.uploads.pop(kw["UploadId"])
        self.objects[key, "v1"] = b"".join(parts[n] for n in sorted(parts))
        if "complete-after" in self.lost:
            raise TimeoutError()
        return {"VersionId": "v1", "ETag": "complete-etag"}

    def abort_multipart_upload(self, **kw):
        self._call("abort", kw)
        if not self.false_abort:
            self.uploads.pop(kw["UploadId"], None)
        return {}

    def list_parts(self, **kw):
        self._call("parts", kw)
        if kw["UploadId"] not in self.uploads:
            raise error("NoSuchUpload", 404)
        return {"Parts": [{"PartNumber": n} for n in self.uploads[kw["UploadId"]][1]]}

    def list_multipart_uploads(self, **kw):
        self._call("uploads", kw)
        return {"IsTruncated": False, "Uploads": [{"Key": key, "UploadId": identifier}
            for identifier, (key, _) in self.uploads.items() if key.startswith(kw["Prefix"])]}


def boundary(sessions, client=None):
    client = client or MultipartS3()
    source = SimpleNamespace(backend_name="s3", encryption_key_id="synthetic-key-id",
        bucket="synthetic-bucket", client=client, _sse={"SSECustomerAlgorithm": "AES256", "SSECustomerKey": b"x" * 32})
    result = VisualStorage(source, sessions=sessions)
    return result, client


def drain(storage):
    future = datetime.now(timezone.utc) + timedelta(minutes=2)
    storage.writes.clock = lambda: future


@pytest.mark.parametrize("lost", ["begin", "part", "complete-before", "complete-after"])
def test_ambiguous_write_restart_cancels_and_completes_without_positive_get(media_db, lost):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    key = "legarya/legacies/1/test/private"
    client.lost.add(lost)
    with pytest.raises(StorageError):
        storage.put(key, b"private synthetic", "audio/wav")
    with sessions() as db:
        row = db.scalar(select(StorageWrite))
        assert row.state == "writing"
        assert bool(row.upload_id) == (lost != "begin")
    restarted, _ = boundary(sessions, client)
    with pytest.raises(StorageError, match="Media storage"):
        restarted.erase(key)
    drain(restarted)
    restarted.writes.sweep()
    assert not client.uploads and not client.objects
    assert not any(name == "get" for name, _ in client.calls)
    with sessions() as db:
        assert db.scalar(select(StorageWrite.state)) == "erased"
    with pytest.raises(StorageError):
        restarted.put(key, b"late write", "audio/wav")


def test_handle_durable_before_private_bytes_and_scope_exact(media_db):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    original = client.upload_part
    def checked(**kw):
        with sessions() as db:
            assert db.scalar(select(StorageWrite.upload_id)) == kw["UploadId"]
        return original(**kw)
    client.upload_part = checked
    key = "legarya/legacies/1/test/private"
    client.uploads["other"] = (key + "-another-owner", {1: b"preserve"})
    result = storage._run(
        "put", key, b"x" * (5 * 1024 * 1024 + 1), "audio/wav")
    assert result.byte_size == 5 * 1024 * 1024 + 1
    assert len([c for c in client.calls if c[0] == "part"]) == 2
    assert all("IfNoneMatch" not in kw for _, kw in client.calls)
    storage.erase(key)
    assert list(client.uploads) == ["other"]
    assert not client.objects


@pytest.mark.parametrize("failure", ["denied", "false-ack", "generic404"])
def test_uncertain_cancel_not_completed_and_retry_recovers(media_db, failure):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    client.lost.add("part")
    key = "legarya/private/cancel"
    with pytest.raises(StorageError):
        storage.put(key, b"x", "audio/wav")
    drain(storage)
    if failure == "denied":
        client.fail["abort"] = error()
    elif failure == "generic404":
        client.fail["parts"] = error("NoSuchBucket", 404)
    else:
        client.false_abort = True
    with pytest.raises(StorageError):
        storage.erase(key)
    with sessions() as db:
        assert db.scalar(select(StorageWrite.state)) == "closing"
    client.fail.clear(); client.false_abort = False
    storage.erase(key)
    assert not client.uploads and not client.objects


def test_late_initiator_cannot_send_bytes_and_sweeper_cancels_empty_upload(media_db):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    key = "legarya/private/late"
    def delete_during_begin():
        drain(storage)
        storage.erase(key)
    client.on_begin = delete_during_begin
    with pytest.raises(StorageError):
        storage.put(key, b"must not transmit", "audio/wav")
    assert not any(name == "part" for name, _ in client.calls)
    # Even a previously accepted create arriving after deletion contains no
    # private parts; durable tombstones continue sweeping it after completion.
    client.uploads["late-empty"] = (key, {})
    current = storage.writes.clock()
    storage.writes.clock = lambda: current + timedelta(hours=2)
    storage.writes.sweep()
    assert not client.uploads


def test_never_received_media_reservation_has_terminal_cancel_path(media_db):
    from app.models.user import User
    from app.services.media_sources import MediaSourceService
    from app.services.media_worker import MediaWorker
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    service = MediaSourceService(storage.storage)
    from uuid import uuid4
    with sessions() as db:
        source = service.create(db, db.get(User, 1), 1, kind="document", filename="synthetic.txt",
            mime_type="text/plain", size_bytes=5, upload_request_key=str(uuid4()))
        service.delete(db, db.get(User, 1), 1, source.id)
    assert MediaWorker(sessions, storage.storage, purge_only=True).run_once() == "purged"
    assert not client.uploads and not client.objects


def test_scope_drift_cannot_erase_wrong_bucket_and_claim_success(media_db):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    key = "legarya/private/scope"
    storage.put(key, b"private synthetic", "audio/wav")
    other, _ = boundary(sessions, client)
    other.storage.bucket = other.bucket_name = "different-bucket"
    from app.services.storage_writes import DurableWrites
    other.writes = DurableWrites(other, sessions)
    with pytest.raises(StorageError) as failure:
        other.erase(key)
    assert failure.value.code == "storage_scope_changed"
    assert client.objects


def test_failed_key_does_not_starve_other_due_cancellations(media_db):
    sessions, _, _ = media_db
    storage, client = boundary(sessions)
    for key in ("legarya/private/a", "legarya/private/b"):
        storage.put(key, b"x", "audio/wav")
        with sessions.begin() as db:
            row = db.get(StorageWrite, storage.writes.identity(key))
            row.state = "closing"
    original = client.abort_multipart_upload
    def one_bad(**kw):
        if kw["Key"].endswith("/a"):
            raise error()
        return original(**kw)
    client.abort_multipart_upload = one_bad
    for _ in range(2):
        storage.writes.sweep(limit=1)
    with sessions() as db:
        assert db.get(StorageWrite, storage.writes.identity("legarya/private/b")).state == "erased"


@pytest.mark.parametrize("endpoint,accepted", [
    ("https://hel1.your-objectstorage.com", True), ("https://storage.invalid", False)])
def test_hetzner_typed_multipart_absence_is_provider_scoped(media_db, endpoint, accepted):
    sessions, _, _ = media_db
    client = MultipartS3()
    client.meta = SimpleNamespace(endpoint_url=endpoint)
    original = client.list_parts
    def rgw(**kw):
        try:
            return original(**kw)
        except Exception as exc:
            if exc.response["Error"]["Code"] == "NoSuchUpload":
                raise error("NoSuchKey", 404)
            raise
    client.list_parts = rgw
    storage, _ = boundary(sessions, client)
    key = "legarya/synthetic/rgw"
    storage.put(key, b"synthetic", "audio/wav")
    if accepted:
        storage.erase(key)
        assert not client.objects
    else:
        with pytest.raises(StorageError):
            storage.erase(key)
        assert client.objects


def test_real_sdk_accepts_encoded_sse_for_every_multipart_operation():
    import base64
    import hashlib
    import time
    import boto3
    from botocore.stub import Stubber
    from app.services.visual_storage import _S3Operations
    raw = b"x" * 32
    encoded = {"SSECustomerAlgorithm": "AES256",
        "SSECustomerKey": base64.b64encode(raw).decode(),
        "SSECustomerKeyMD5": base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode()}
    client = boto3.client("s3", endpoint_url="https://storage.invalid", region_name="hel1",
        aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    key, identifier, bucket = "synthetic/key", "synthetic-upload", "synthetic-bucket"
    ops = _S3Operations(client, bucket, {"SSECustomerAlgorithm": "AES256", "SSECustomerKey": raw}, time.monotonic() + 30)
    with Stubber(client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": identifier},
            {"Bucket": bucket, "Key": key, "ContentType": "audio/wav", **encoded})
        stub.add_response("upload_part", {"ETag": "synthetic-etag"},
            {"Bucket": bucket, "Key": key, "UploadId": identifier, "PartNumber": 1, "Body": b"x", **encoded})
        parts = [{"PartNumber": 1, "ETag": "synthetic-etag"}]
        stub.add_response("complete_multipart_upload", {"VersionId": "v1"},
            {"Bucket": bucket, "Key": key, "UploadId": identifier, "MultipartUpload": {"Parts": parts}, **encoded})
        stub.add_client_error("abort_multipart_upload", service_error_code="NoSuchUpload", http_status_code=404,
            expected_params={"Bucket": bucket, "Key": key, "UploadId": identifier})
        stub.add_client_error("list_parts", service_error_code="NoSuchUpload", http_status_code=404,
            expected_params={"Bucket": bucket, "Key": key, "UploadId": identifier, "MaxParts": 1, **encoded})
        for _ in range(3):
            stub.add_response("list_multipart_uploads", {"IsTruncated": False},
                {"Bucket": bucket, "Prefix": key, "MaxUploads": 100})
        assert ops.multipart_begin(key, "audio/wav") == identifier
        assert ops.multipart_part(key, b"x", identifier, 1) == "synthetic-etag"
        assert ops.multipart_complete(key, identifier, parts)["VersionId"] == "v1"
        ops.multipart_cancel(key, identifier)
        stub.assert_no_pending_responses()
    client.close()
