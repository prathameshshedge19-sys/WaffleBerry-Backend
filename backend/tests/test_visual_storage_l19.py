"""Offline L19 safety tests: temp local files, injected S3 and loopback HTTP.

No real bucket, production configuration, or account credentials are used.
Disposable real S3/SSE-C acceptance is NOT PERFORMED (no test bucket supplied).
"""

import hashlib
import io
import multiprocessing
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.services import visual_storage as module
from app.services.media_storage import LocalSourceStorage, StorageError, StoredObject
from app.services.visual_storage import MAX_ASSET_BYTES, MAX_ORIGINAL_BYTES, VisualStorage

KEY = "visual/version/attempt/poster"
DATA = b"synthetic portrait bytes"
SHA = hashlib.sha256(DATA).hexdigest()


def error(code="AccessDenied", status=403):
    return ClientError({"Error": {"Code": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Fake")


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail = {}
        self.pages = None
        self.body = None
        self.partial_delete = False

    def _call(self, name, kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise self.fail[name]

    def put_object(self, **kwargs):
        self._call("put", kwargs)
        if any(k == kwargs["Key"] and value is not None for (k, _), value in self.objects.items()):
            raise error("PreconditionFailed", 412)
        self.objects[kwargs["Key"], "v1"] = kwargs["Body"]
        return {"VersionId": "v1", "ETag": '"not-a-sha256"'}

    def get_object(self, **kwargs):
        self._call("get", kwargs)
        matches = [(version, data) for (key, version), data in self.objects.items()
                   if key == kwargs["Key"] and
                   ("VersionId" not in kwargs or kwargs["VersionId"] == version)]
        if not matches or matches[-1][1] is None:
            raise error("NoSuchKey", 404)
        version, data = matches[-1]
        self.body = io.BytesIO(data)
        return {"Body": self.body, "ContentLength": len(data), "VersionId": version}

    def head_object(self, **kwargs):
        self._call("head", kwargs)
        matches = [data for (key, _), data in self.objects.items() if key == kwargs["Key"]]
        if not matches or matches[-1] is None:
            raise error("404", 404)
        return {"ContentLength": len(matches[-1])}

    def list_object_versions(self, **kwargs):
        self._call("list", kwargs)
        if self.pages is not None:
            return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        entries = sorted((key, version, data) for (key, version), data in self.objects.items()
                         if key.startswith(kwargs["Prefix"]))
        if "KeyMarker" in kwargs:
            entries = [entry for entry in entries if entry[:2] >
                       (kwargs["KeyMarker"], kwargs["VersionIdMarker"])]
        limit = kwargs["MaxKeys"]
        page = {"IsTruncated": len(entries) > limit, "Versions": [], "DeleteMarkers": []}
        for key, version, data in entries[:limit]:
            page["DeleteMarkers" if data is None else "Versions"].append({"Key": key, "VersionId": version})
        if page["IsTruncated"]:
            page["NextKeyMarker"], page["NextVersionIdMarker"] = entries[limit - 1][:2]
        return page

    def delete_objects(self, **kwargs):
        self._call("delete_batch", kwargs)
        if self.partial_delete:
            return {"Errors": [{"Key": KEY, "Code": "AccessDenied"}]}
        for item in kwargs["Delete"]["Objects"]:
            self.objects.pop((item["Key"], item["VersionId"]), None)
        return {"Deleted": kwargs["Delete"]["Objects"]}

    def delete_object(self, **kwargs):
        self._call("delete", kwargs)
        self.objects.pop((kwargs["Key"], kwargs["VersionId"]), None)
        return {}


@pytest.fixture
def s3():
    client = FakeS3()
    source = SimpleNamespace(backend_name="s3", encryption_key_id="test-key-id",
                             bucket="fake-bucket", client=client,
                             _sse={"SSECustomerAlgorithm": "AES256", "SSECustomerKey": b"x" * 32})
    return VisualStorage(source), client


@pytest.fixture
def local(tmp_path):
    return VisualStorage(LocalSourceStorage(str(tmp_path / "objects")))


def test_local_lifecycle_and_l16_unchanged(local):
    assert local.put(KEY, DATA, "image/png") == StoredObject(len(DATA))
    assert local.storage.exists(KEY)
    assert local.read(KEY) == DATA
    assert local.verify(KEY, SHA.upper(), len(DATA))
    assert not local.verify(KEY, "0" * 64, len(DATA))
    assert not local.verify(KEY, SHA, len(DATA) + 1)
    with pytest.raises(StorageError, match="Media storage") as failure:
        local.put(KEY, DATA, "image/png")
    assert failure.value.code == "storage_object_exists"
    unrelated = KEY + "-other"
    local.storage.put(unrelated, b"original", content_type="application/octet-stream")
    assert local.erase(KEY) is None
    assert local.erase(KEY) is None
    assert not local.verify(KEY, SHA, len(DATA))
    with local.storage.open(unrelated) as body:
        assert body.read() == b"original"


@pytest.mark.parametrize("key", ["", "/", "../original", "visual/../original", "a//b", "a/./b",
                                  "a/", "a\\b", "C:/original", "a\x00b", "a\nb", "x" * 1025,
                                  "\ud800"])
def test_invalid_keys_never_touch_storage(s3, key):
    storage, client = s3
    for action in (lambda: storage.put(key, DATA, "image/png"),
                   lambda: storage.read(key), lambda: storage.verify(key, SHA, len(DATA)),
                   lambda: storage.erase(key)):
        with pytest.raises(StorageError) as failure:
            action()
        assert failure.value.code == "storage_key_invalid"
    assert not client.calls


def test_size_bounds(local, s3):
    data = b"x" * MAX_ASSET_BYTES
    local.put(KEY, data, "image/png")
    assert local.read(KEY) == data
    assert local.verify(KEY, hashlib.sha256(data).hexdigest(), len(data))
    for invalid in (b"", data + b"x", bytearray(b"x")):
        with pytest.raises(StorageError):
            s3[0].put(KEY, invalid, "image/png")
    assert not s3[1].calls
    local.storage.put(KEY + "-oversize", data + b"x", content_type="image/png")
    assert not local.verify(KEY + "-oversize", SHA, len(DATA))


@pytest.mark.parametrize("sha,size", [("bad", 1), (SHA, 0), (SHA, -1), (SHA, True),
                                     (SHA, MAX_ASSET_BYTES + 1)])
def test_invalid_verification_metadata(s3, sha, size):
    with pytest.raises(StorageError):
        s3[0].verify(KEY, sha, size)
    assert not s3[1].calls


def test_s3_read_verifies_actual_bytes_version_and_sse(s3):
    storage, client = s3
    stored = storage.put(KEY, DATA, "image/png")
    assert stored == StoredObject(len(DATA), '"not-a-sha256"', "v1")
    client.objects[KEY, "v2"] = b"different"
    assert storage.verify(KEY, SHA, len(DATA), "v1")
    assert client.body.closed
    assert not storage.verify(KEY, SHA, len(DATA))
    assert not storage.verify(KEY, SHA, len(DATA), "missing")
    for name, args in client.calls:
        assert args["Bucket"] == "fake-bucket"
        if name in ("put", "get"):
            assert args["SSECustomerAlgorithm"] == "AES256"
            assert args["SSECustomerKey"] == b"x" * 32
    assert client.calls[0][1]["IfNoneMatch"] == "*"


def test_original_reader_accepts_exact_20_mib_and_preserves_generated_limit(s3, local):
    storage, client = s3
    original = b'o' * MAX_ORIGINAL_BYTES
    client.objects[KEY, 'original-v1'] = original
    assert storage.read_original(KEY, 'original-v1') == original
    name, args = client.calls[-1]
    assert name == 'get' and args['VersionId'] == 'original-v1'
    assert args['Bucket'] == 'fake-bucket' and args['SSECustomerKey'] == b'x'*32
    assert client.body.closed
    with pytest.raises(StorageError) as caught:
        storage.read(KEY, 'original-v1')
    assert caught.value.code == 'storage_size_exceeded'
    local.storage.put(KEY, original, content_type='image/png')
    assert local.read_original(KEY) == original


@pytest.mark.parametrize('declared,actual,version,code', [
    (MAX_ORIGINAL_BYTES+1, 1, 'v1', 'storage_size_exceeded'),
    (1, MAX_ORIGINAL_BYTES+1, 'v1', 'storage_size_exceeded'),
    (2, 1, 'v1', 'storage_read_failed'),
    (None, 1, 'v1', 'storage_read_failed'),
    (1, 1, 'v2', 'storage_version_mismatch')])
def test_original_reader_bounds_metadata_and_version(s3, monkeypatch, declared, actual, version, code):
    storage, client = s3
    class Body(io.BytesIO):
        consumed = 0
        def read(self, count):
            assert 0 < count <= 65536
            chunk = super().read(count)
            self.consumed += len(chunk)
            return chunk
    body = Body(b'x'*actual)
    monkeypatch.setattr(client, 'get_object', lambda **kwargs:
        {'Body':body, 'ContentLength':declared, 'VersionId':version})
    with pytest.raises(StorageError) as caught:
        storage.read_original(KEY, 'v1')
    assert caught.value.code == code
    assert body.closed and body.consumed <= MAX_ORIGINAL_BYTES+1


def test_reconcile_requires_positive_exact_bytes_not_absence_or_neighbor(s3):
    storage, client = s3
    assert storage.reconcile_write(KEY, SHA, len(DATA)) is False
    client.objects[KEY+'-neighbor', 'v1'] = DATA
    client.objects[KEY, 'wrong'] = b'wrong bytes'
    client.objects[KEY, 'marker'] = None
    assert storage.reconcile_write(KEY, SHA, len(DATA)) is False
    client.objects[KEY, 'late'] = DATA
    assert storage.reconcile_write(KEY, SHA, len(DATA)) is True
    assert not any(name.startswith('delete') for name, _ in client.calls)
    assert all(args.get('Key', KEY) == KEY for _, args in client.calls)


@pytest.mark.parametrize('operation', ['get', 'list'])
def test_reconcile_transport_failure_is_not_completion(s3, operation):
    storage, client = s3
    client.objects[KEY, 'v1'] = DATA
    client.fail[operation] = error()
    with pytest.raises(StorageError):
        storage.reconcile_write(KEY, SHA, len(DATA))


@pytest.mark.parametrize('checksum,size', [(None, 1), ([], 1), ('bad', 1), (SHA, True), (SHA, 0)])
def test_reconcile_invalid_registry_rejected_before_io(s3, checksum, size):
    storage, client = s3
    with pytest.raises(StorageError) as caught:
        storage.reconcile_write(KEY, checksum, size)
    assert caught.value.code == 'storage_verification_invalid'
    assert not client.calls


@pytest.mark.parametrize("failure", [error(), error("NoSuchBucket", 404), error("BadGateway", 502),
                                     TimeoutError(), ConnectionError()])
def test_uncertain_reads_raise(s3, failure):
    storage, client = s3
    client.fail["get"] = failure
    with pytest.raises(StorageError) as caught:
        storage.verify(KEY, SHA, len(DATA))
    assert caught.value.code == "storage_read_failed"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("size,version,code", [(MAX_ASSET_BYTES + 1, "v1", "storage_size_exceeded"),
                                            (None, "v1", "storage_read_failed"),
                                            (len(DATA), None, "storage_read_failed"),
                                            (len(DATA), "wrong", "storage_version_mismatch"),
                                            (len(DATA) + 1, "v1", "storage_read_failed")])
def test_bad_s3_metadata_closes_body(s3, monkeypatch, size, version, code):
    storage, client = s3
    body = io.BytesIO(DATA)
    monkeypatch.setattr(client, "get_object", lambda **kwargs:
                        {"Body": body, "ContentLength": size, "VersionId": version})
    with pytest.raises(StorageError) as caught:
        storage.read(KEY, "v1")
    assert caught.value.code == code
    assert body.closed


def test_stream_is_bounded_even_when_length_lies(s3, monkeypatch):
    storage, client = s3
    class Body(io.BytesIO):
        requests = []
        def read(self, count):
            self.requests.append(count)
            assert 0 < count <= 65536
            return super().read(count)
    body = Body(b"x" * (MAX_ASSET_BYTES + 100))
    monkeypatch.setattr(client, "get_object", lambda **kwargs:
                        {"Body": body, "ContentLength": 1})
    with pytest.raises(StorageError) as caught:
        storage.read(KEY)
    assert caught.value.code == "storage_size_exceeded"
    assert sum(body.requests) == MAX_ASSET_BYTES + 1
    assert body.closed


def test_broken_stream_is_uncertain_and_closed(s3, monkeypatch):
    storage, client = s3
    class Broken(io.BytesIO):
        def read(self, count):
            raise TimeoutError("private transport details")
    body = Broken()
    monkeypatch.setattr(client, "get_object", lambda **kwargs:
                        {"Body": body, "ContentLength": 1})
    with pytest.raises(StorageError) as caught:
        storage.verify(KEY, SHA, len(DATA))
    assert caught.value.code == "storage_read_failed"
    assert "private" not in str(caught.value)
    assert body.closed


def test_erase_all_versions_markers_and_exact_key_only(s3, monkeypatch):
    storage, client = s3
    monkeypatch.setattr(module, "VERSION_PAGE_SIZE", 2)
    client.objects = {(KEY, "null"): DATA, (KEY, "v1"): DATA, (KEY, "v2"): None,
                      (KEY, "v3"): DATA, (KEY, "v4"): None,
                      (KEY + "-neighbor", "v1"): b"unrelated",
                      ("original/photo", "v1"): b"original"}
    storage.erase(KEY)
    assert client.objects == {(KEY + "-neighbor", "v1"): b"unrelated",
                              ("original/photo", "v1"): b"original"}
    deleted = [item for name, kwargs in client.calls if name == "delete_batch"
               for item in kwargs["Delete"]["Objects"]]
    assert {item["VersionId"] for item in deleted} == {"null", "v1", "v2", "v3", "v4"}
    assert all(item["Key"] == KEY for item in deleted)
    assert any(name == "list" and "VersionIdMarker" in kwargs for name, kwargs in client.calls)
    storage.erase(KEY)


@pytest.mark.parametrize("operation", ["list", "head", "delete_batch"])
def test_erase_errors_are_not_absence(s3, operation):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    client.fail[operation] = error()
    with pytest.raises(StorageError):
        storage.erase(KEY)


def test_partial_batch_delete_remains_retryable(s3):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    client.partial_delete = True
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_delete_failed"
    client.partial_delete = False
    storage.erase(KEY)
    assert not client.objects


def test_missing_delete_acknowledgement_is_uncertain(s3, monkeypatch):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    monkeypatch.setattr(client, "delete_objects", lambda **kwargs: {})
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_delete_uncertain"


def test_success_acknowledgement_without_actual_deletion_is_uncertain(s3, monkeypatch):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    monkeypatch.setattr(client, "delete_objects", lambda **kwargs:
                        {"Deleted": kwargs["Delete"]["Objects"]})
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_delete_uncertain"


def test_final_absence_check_error_is_uncertain(s3, monkeypatch):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    original_head = client.head_object
    calls = 0
    def head(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error()
        return original_head(**kwargs)
    monkeypatch.setattr(client, "head_object", head)
    with pytest.raises(StorageError):
        storage.erase(KEY)
    assert not client.objects
    assert calls == 2


@pytest.mark.parametrize("page", [{}, {"IsTruncated": "false"},
    {"IsTruncated": False, "Versions": [{"Key": KEY}]},
    {"IsTruncated": True},
    {"IsTruncated": True, "NextKeyMarker": KEY, "NextVersionIdMarker": "v1"}])
def test_malformed_or_repeated_pagination_fails_closed(s3, page):
    storage, client = s3
    client.pages = [page]
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_version_listing_invalid"
    assert len(client.calls) <= 2


def test_page_budget_makes_progress_but_never_claims_erased(s3, monkeypatch):
    storage, client = s3
    monkeypatch.setattr(module, "MAX_VERSION_PAGES", 1)
    monkeypatch.setattr(module, "VERSION_PAGE_SIZE", 1)
    client.objects = {(KEY, "v1"): DATA, (KEY, "v2"): None}
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_version_limit"
    assert client.objects == {(KEY, "v2"): None}
    storage.erase(KEY)
    assert not client.objects


def test_late_version_requires_another_sweep(s3, monkeypatch):
    storage, client = s3
    client.objects[KEY, "v1"] = DATA
    original_head = client.head_object
    def late_put(**kwargs):
        client.objects[KEY, "late"] = DATA
        return original_head(**kwargs)
    monkeypatch.setattr(client, "head_object", late_put)
    with pytest.raises(StorageError) as caught:
        storage.erase(KEY)
    assert caught.value.code == "storage_delete_uncertain"
    monkeypatch.setattr(client, "head_object", original_head)
    storage.erase(KEY)
    assert not client.objects


def test_unversioned_fallback_deletes_only_null_and_verifies(s3):
    storage, client = s3
    client.objects[KEY, "null"] = DATA
    client.pages = [{"IsTruncated": False}]
    storage.erase(KEY)
    assert not client.objects
    deletes = [args for name, args in client.calls if name == "delete"]
    assert deletes == [{"Bucket": "fake-bucket", "Key": KEY, "VersionId": "null"}]


def test_generic_fake_supported_without_exists():
    class Fake:
        backend_name, encryption_key_id = "memory", None
        data = {}
        def put(self, key, data, *, content_type):
            self.data[key] = data
            return StoredObject(len(data))
        def open(self, key):
            if key not in self.data:
                raise StorageError("storage_not_found")
            return io.BytesIO(self.data[key])
        def delete(self, key):
            self.data.pop(key, None)
        def exists(self, key):
            pytest.fail("exists is not proof")
    storage = VisualStorage(Fake())
    storage.put(KEY, DATA, "image/png")
    assert storage.verify(KEY, SHA, len(DATA))
    storage.erase(KEY)
    assert not storage.verify(KEY, SHA, len(DATA))


def test_local_versions_fail_closed(local):
    local.put(KEY, DATA, "image/png")
    with pytest.raises(StorageError) as caught:
        local.verify(KEY, SHA, len(DATA), "v1")
    assert caught.value.code == "storage_version_unsupported"


def test_local_erase_permission_error_is_uncertain(local, monkeypatch):
    def denied(key):
        raise PermissionError("private path")
    monkeypatch.setattr(local.storage, "open", denied)
    with pytest.raises(StorageError) as caught:
        local.erase(KEY)
    assert caught.value.code == "storage_erase_failed"


@pytest.mark.parametrize("key", ["visual/NUL", "visual/poster.", "visual/poster "])
def test_local_path_aliases_are_rejected(local, key):
    with pytest.raises(StorageError) as caught:
        local.erase(key)
    assert caught.value.code == "storage_key_invalid"


def test_local_link_alias_is_rejected(local, monkeypatch):
    import stat
    from pathlib import Path
    local.put(KEY, DATA, "image/png")
    original = Path.lstat
    def linked(path):
        if path.name == "poster":
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path)
    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(StorageError) as caught:
        local.erase(KEY)
    assert caught.value.code == "storage_key_invalid"
    assert local.storage.exists(KEY)


@pytest.mark.parametrize('reader', ['read', 'read_original'])
def test_trickling_stream_obeys_total_deadline(s3, monkeypatch, reader):
    storage, client = s3
    now = [0]
    class SlowBody(io.BytesIO):
        def read(self, size):
            now[0] += 10
            return super().read(1)
    body = SlowBody(DATA)
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(client, "get_object", lambda **kwargs:
                        {"Body": body, "ContentLength": len(DATA)})
    with pytest.raises(StorageError) as caught:
        getattr(storage, reader)(KEY)
    assert caught.value.code == "storage_timeout"
    assert now[0] == 30
    assert body.closed


def test_no_dispatch_after_deadline(s3, monkeypatch):
    storage, client = s3
    ticks = iter([0, 31])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(StorageError) as caught:
        storage.put(KEY, DATA, "image/png")
    assert caught.value.code == "storage_timeout"
    assert not client.calls


def test_timed_out_put_is_never_retried(s3):
    storage, client = s3
    client.fail["put"] = TimeoutError()
    with pytest.raises(StorageError):
        storage.put(KEY, DATA, "image/png")
    assert len(client.calls) == 1
    assert storage.writer_window_seconds == 120


def _delayed_child(connection, output, client_args, bucket, sse, operation, args, deadline):
    # Would write after the caller timed out if the process were merely abandoned.
    from pathlib import Path
    Path(args[0] + ".started").touch()
    time.sleep(3)
    Path(args[0]).touch()


def _blocked_writer_child(connection, output, client_args, bucket, sse, operation, args, deadline):
    from pathlib import Path
    sse["started"].set()
    limit = time.monotonic() + 30
    while time.monotonic() < limit:
        if Path(sse["release_path"]).exists():
            Path(args[0]).touch()
            return
        time.sleep(.01)


def _offline_client(endpoint="http://127.0.0.1:1"):
    return boto3.session.Session().client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id="offline-test", aws_secret_access_key="offline-test",
        config=Config(connect_timeout=60, read_timeout=60,
                      retries={"total_max_attempts": 8}, s3={"addressing_style": "path"}))


def _wrap_client(client):
    return VisualStorage(SimpleNamespace(client=client, bucket="fake-bucket", _sse={},
                                         backend_name="s3", encryption_key_id="test-key-id"))


def test_hard_timeout_includes_startup_and_preserves_l16_client(tmp_path, monkeypatch):
    client = _offline_client()
    storage = _wrap_client(client)
    before = {process.pid for process in multiprocessing.active_children()}
    monkeypatch.setattr(module, "_s3_child", _delayed_child)
    monkeypatch.setattr(module, "OPERATION_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    with pytest.raises(StorageError) as caught:
        storage._run("put", str(tmp_path / "late-write"), DATA, "image/png")
    assert caught.value.code == "storage_timeout"
    assert time.monotonic() - started < 4
    assert {process.pid for process in multiprocessing.active_children()} == before
    # Startup is part of the deadline: on a loaded host the process may be
    # correctly killed before Python reaches the target. The next test proves
    # termination of an already-running writer using an explicit handshake.
    assert not (tmp_path / "late-write").exists()
    assert client.meta.config.connect_timeout == 60
    assert client.meta.config.read_timeout == 60
    assert client.meta.config.retries["total_max_attempts"] == 8
    client.close()


def test_response_timeout_terminates_a_confirmed_running_writer(tmp_path, monkeypatch):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release_path = tmp_path / "release-writer"
    client = _offline_client()
    storage = _wrap_client(client)
    storage.storage._sse = {"started": entered, "release_path": str(release_path)}
    before = {process.pid for process in multiprocessing.active_children()}

    class TimeoutReceiver:
        def __init__(self, receiver):
            self.receiver = receiver
        def poll(self, timeout):
            # Simulate the response deadline only AFTER an independent spawned
            # writer has entered. No assumption about a two-second boot time.
            assert entered.wait(min(timeout, 20)), "Spawned writer did not enter"
            return False
        def close(self):
            self.receiver.close()

    class ControlledContext:
        Process = context.Process
        RawArray = context.RawArray
        @staticmethod
        def Pipe(duplex):
            receiver, sender = context.Pipe(duplex=duplex)
            return TimeoutReceiver(receiver), sender

    monkeypatch.setattr(module, "_s3_child", _blocked_writer_child)
    monkeypatch.setattr(module.multiprocessing, "get_context", lambda method: ControlledContext())
    started = time.monotonic()
    try:
        with pytest.raises(StorageError) as caught:
            storage._run("put", str(tmp_path / "confirmed-late-write"), DATA, "image/png")
        assert caught.value.code == "storage_timeout"
        assert entered.is_set()
        assert time.monotonic() - started < 32  # Production 30s + shutdown bound.
        assert {process.pid for process in multiprocessing.active_children()} == before
        # Never reuse a lock/condition a forcibly killed process could hold.
        release_path.touch()  # An abandoned writer could now write.
        assert not (tmp_path / "confirmed-late-write").exists()
    finally:
        release_path.touch()
        client.close()


def test_real_sdk_spawn_loopback_read_and_no_retries():
    class Handler(BaseHTTPRequestHandler):
        calls = []
        writes = []
        fail = False
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.calls.append(self.path)
            if self.fail:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.send_header("x-amz-version-id", "v1")
            self.end_headers()
            self.wfile.write(DATA)
        def do_PUT(self):
            self.writes.append(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["If-None-Match"] == "*"
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.send_header("x-amz-version-id", "v1")
            self.send_header("ETag", '"fake"')
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = _offline_client(f"http://127.0.0.1:{server.server_port}")
    storage = _wrap_client(client)
    try:
        assert storage.put(KEY, DATA, "image/png") == StoredObject(len(DATA), '"fake"', "v1")
        assert Handler.writes == [DATA]
        assert storage.read(KEY, "v1") == DATA
        assert len(Handler.calls) == 1
        Handler.fail = True
        with pytest.raises(StorageError):
            storage.read(KEY)
        assert len(Handler.calls) == 2  # one request, despite L16's 8-attempt config
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
