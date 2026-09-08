"""L19 safety boundary; does not alter the L16 SourceStorage contract.

Workers reserve an immutable attempt key BEFORE put, retain a durable 120-second
writer window, and dispatch only with >32s remaining before its deadline.
A failed/timed-out put is uncertain: do not retry that key. Keep its reservation
and sweep with erase after the writer deadline, including after worker crashes.
erase proves absence at inspection time, not that a concurrent writer cannot
arrive later. The caller owns leases, writer admission and durable cleanup.

Real S3 operations use a fresh client in a terminable spawned process: 3s connect,
5s socket read, one SDK attempt, 30s whole-operation deadline (including reads and
pagination), plus at most 2s process shutdown. No abandoned writer threads.
Already accepted remote PUTs still require post-window reconciliation. These are
client execution bounds, not a promise about a remote server's commit latency.

Local storage and injected in-memory SourceStorage/S3 fakes run synchronously;
fakes must implement truthful not-found errors and bounded, non-network methods.
Production network adapters must use S3SourceStorage. No settings/credential
discovery, bucket changes or L16 client mutation happens here.
"""

from __future__ import annotations

import hashlib
import hmac
import multiprocessing
import re
import stat
import time
from pathlib import PurePosixPath, PureWindowsPath

from app.services.media_storage import LocalSourceStorage, SourceStorage, StorageError, StoredObject

MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_ORIGINAL_BYTES = 20 * 1024 * 1024
WRITER_WINDOW_SECONDS = 120
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
OPERATION_TIMEOUT_SECONDS = 30
PROCESS_STOP_TIMEOUT_SECONDS = 1
MAX_OPERATION_SECONDS = OPERATION_TIMEOUT_SECONDS + 2 * PROCESS_STOP_TIMEOUT_SECONDS
MAX_VERSION_PAGES = 10
VERSION_PAGE_SIZE = 100
_CHUNK_BYTES = 64 * 1024


def _key(key: str) -> str:
    # Never normalize aliases: the registry and storage must mean the same key.
    try:
        encoded_size = len(key.encode("utf-8")) if isinstance(key, str) else 0
    except UnicodeError:
        raise StorageError("storage_key_invalid") from None
    if (not isinstance(key, str) or not key or encoded_size > 1024
            or key.startswith("/") or "\\" in key or ":" in key
            or any(ord(c) < 32 or ord(c) == 127 for c in key)
            or any(p in ("", ".", "..") for p in key.split("/"))
            or PurePosixPath(key).is_absolute()):
        raise StorageError("storage_key_invalid")
    return key


def _local_key(storage: LocalSourceStorage, key: str) -> None:
    # Local storage roots are private worker-owned directories. Refuse existing
    # symlink/junction aliases and special files rather than following them to
    # another registered object (or blocking forever on a FIFO).
    path = storage.root
    for part in key.split("/"):
        if part.endswith((".", " ")) or PureWindowsPath(part).is_reserved():
            raise StorageError("storage_key_invalid")
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
            raise StorageError("storage_key_invalid")


def _version(version: str | None) -> None:
    if version is not None and (not isinstance(version, str) or not version):
        raise StorageError("storage_version_invalid")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise StorageError("storage_timeout")


def _missing(exc: Exception) -> bool:
    # Do not treat generic HTTP 404, NoSuchBucket, 403, or transport failures as
    # proof of missing content. S3 HEAD uses the numeric code for missing keys.
    response = getattr(exc, "response", {})
    return (isinstance(response, dict)
            and response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
            and response.get("Error", {}).get("Code") in
            ("NoSuchKey", "NoSuchVersion", "NotFound", "404"))


def _read_body(body, deadline: float, maximum=MAX_ASSET_BYTES) -> bytes:
    data = bytearray()
    try:
        while True:
            _check_deadline(deadline)
            chunk = body.read(min(_CHUNK_BYTES, maximum + 1 - len(data)))
            _check_deadline(deadline)
            if not isinstance(chunk, bytes):
                raise StorageError("storage_read_failed")
            if not chunk:
                return bytes(data)
            if len(data) + len(chunk) > maximum:
                raise StorageError("storage_size_exceeded")
            data.extend(chunk)
    finally:
        body.close()


class _S3Operations:
    def __init__(self, client, bucket: str, sse: dict, deadline: float):
        self.client, self.bucket, self.sse, self.deadline = client, bucket, sse, deadline

    def call(self, method: str, **kwargs):
        _check_deadline(self.deadline)
        result = getattr(self.client, method)(Bucket=self.bucket, **kwargs)
        if time.monotonic() >= self.deadline:
            if isinstance(result, dict) and "Body" in result:
                result["Body"].close()
            raise StorageError("storage_timeout")
        return result

    def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        # Conditional write closes a duplicate-dispatch race. Per-attempt keys
        # and writer fencing are still required (including after delete markers).
        result = self.call("put_object", Key=key, Body=data, ContentType=mime,
                           IfNoneMatch="*", **self.sse)
        return StoredObject(len(data), result.get("ETag"), result.get("VersionId"))

    def read(self, key: str, version: str | None) -> bytes:
        return self._read(key, version, MAX_ASSET_BYTES)

    def read_original(self, key: str, version: str | None) -> bytes:
        return self._read(key, version, MAX_ORIGINAL_BYTES)

    def _read(self, key, version, maximum):
        args = {"Key": key, **self.sse}
        if version is not None:
            args["VersionId"] = version
        try:
            result = self.call("get_object", **args)
        except Exception as exc:
            if _missing(exc):
                raise StorageError("storage_not_found") from None
            raise
        body = result["Body"]
        try:
            size = result.get("ContentLength")
            if type(size) is not int or size < 0:
                raise StorageError("storage_read_failed")
            if size > maximum:
                raise StorageError("storage_size_exceeded")
            if version is not None:
                if not isinstance(result.get("VersionId"), str) or not result["VersionId"]:
                    raise StorageError("storage_read_failed")
                if result["VersionId"] != version:
                    raise StorageError("storage_version_mismatch")
        except Exception:
            body.close()
            raise
        data = _read_body(body, self.deadline, maximum)
        if len(data) != size:
            raise StorageError("storage_read_failed")
        return data

    def _versions(self, key: str):
        markers = {}
        seen = set()
        for _ in range(MAX_VERSION_PAGES):
            page = self.call("list_object_versions", Prefix=key,
                             MaxKeys=VERSION_PAGE_SIZE, **markers)
            if type(page.get("IsTruncated")) is not bool:
                raise StorageError("storage_version_listing_invalid")
            objects = []
            for field in ("Versions", "DeleteMarkers"):
                entries = page.get(field, [])
                if not isinstance(entries, list):
                    raise StorageError("storage_version_listing_invalid")
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("Key"), str):
                        raise StorageError("storage_version_listing_invalid")
                    if entry["Key"] != key:
                        continue
                    version = entry.get("VersionId")
                    if not isinstance(version, str) or not version:
                        raise StorageError("storage_version_listing_invalid")
                    objects.append({"Key": key, "VersionId": version})
            if len(page.get("Versions", [])) + len(page.get("DeleteMarkers", [])) > VERSION_PAGE_SIZE:
                raise StorageError("storage_version_listing_invalid")
            yield objects
            if not page["IsTruncated"]:
                return
            next_key, next_version = page.get("NextKeyMarker"), page.get("NextVersionIdMarker")
            if (not isinstance(next_key, str) or not next_key.startswith(key)
                    or not isinstance(next_version, str) or not next_version
                    or (next_key, next_version) in seen):
                raise StorageError("storage_version_listing_invalid")
            seen.add((next_key, next_version))
            markers = {"KeyMarker": next_key, "VersionIdMarker": next_version}
        raise StorageError("storage_version_limit")

    def _absent(self, key: str) -> bool:
        try:
            self.call("head_object", Key=key, **self.sse)
        except Exception as exc:
            if _missing(exc):
                return True
            raise
        return False

    def erase(self, key: str) -> None:
        for objects in self._versions(key):
            if objects:
                result = self.call("delete_objects", Delete={"Objects": objects, "Quiet": False})
                if result.get("Errors"):
                    raise StorageError("storage_delete_failed")
                deleted = {(item.get("Key"), item.get("VersionId"))
                           for item in result.get("Deleted", [])}
                if deleted != {(key, obj["VersionId"]) for obj in objects}:
                    raise StorageError("storage_delete_uncertain")
        # Unversioned implementations may return an empty version listing while
        # a current object exists. Deleting VersionId=null avoids making a new
        # delete marker if bucket versioning changed concurrently.
        if not self._absent(key):
            self.call("delete_object", Key=key, VersionId="null")
        for objects in self._versions(key):
            if objects:
                raise StorageError("storage_delete_uncertain")
        if not self._absent(key):
            raise StorageError("storage_delete_uncertain")

    def reconcile_write(self, key, sha256, byte_size):
        """A matching committed object resolves ONE no-retry immutable PUT.

        Absence is never a terminal request outcome. Caller must persist this
        positive observation before erasing; otherwise a crash loses the proof.
        """
        versions = [obj['VersionId'] for page in self._versions(key) for obj in page]
        for version in versions or [None]:
            try:
                data = self.read(key, version)
            except StorageError as exc:
                if exc.code == 'storage_not_found':
                    continue  # Delete markers are not successful write proof.
                raise
            except Exception as exc:
                response = getattr(exc, 'response', {})
                headers = response.get('ResponseMetadata', {}).get('HTTPHeaders', {})
                if (response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 405
                        and headers.get('x-amz-delete-marker') == 'true'):
                    continue
                raise
            if len(data) == byte_size and hmac.compare_digest(hashlib.sha256(data).hexdigest(), sha256):
                return True
        return False


def _s3_child(connection, output, client_args: dict, bucket: str, sse: dict,
              operation: str, args: tuple, deadline: float) -> None:
    """Spawn target: no DB imports, settings lookup, log bodies or raw errors."""
    client = None
    try:
        import boto3

        _check_deadline(deadline)
        client = boto3.session.Session().client("s3", **client_args)
        if operation == "put":
            key, size, mime = args
            args = (key, bytes(output[:size]), mime)
        result = getattr(_S3Operations(client, bucket, sse, deadline), operation)(*args)
        if isinstance(result, bytes):
            output[:len(result)] = result
            result = len(result)
        connection.send((True, result))
    except Exception as exc:
        code = exc.code if isinstance(exc, StorageError) else "storage_" + operation + "_failed"
        connection.send((False, code))
    finally:
        connection.close()
        if client is not None:
            client.close()


class VisualStorage:
    """Wrap an existing configured SourceStorage; no production probes on init.

    read returns <=2 MiB; verify hashes actual bytes (ETag is not a SHA-256).
    False means confirmed missing/mismatching bytes or version. Uncertain I/O
    raises StorageError. Local/fake adapters have no object versions; supplying
    a version there fails closed. erase never uses SourceStorage.exists().
    Bundle-total and role-specific size limits remain the caller's responsibility.
    """

    writer_window_seconds = WRITER_WINDOW_SECONDS
    operation_timeout_seconds = OPERATION_TIMEOUT_SECONDS
    max_operation_seconds = MAX_OPERATION_SECONDS
    max_asset_bytes = MAX_ASSET_BYTES

    def __init__(self, storage: SourceStorage):
        self.storage = storage
        self.backend_name = storage.backend_name
        self.encryption_key_id = storage.encryption_key_id
        self.bucket_name = getattr(storage, 'bucket', None)

    def _run(self, operation: str, *args):
        deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS
        try:
            if isinstance(self.storage, LocalSourceStorage):
                _local_key(self.storage, args[0])
            _check_deadline(deadline)
            if self.backend_name == "s3":
                from botocore.client import BaseClient

                if isinstance(self.storage.client, BaseClient):
                    return self._isolated_s3(operation, args, deadline)
                # Injected deterministic fake; do not rebuild it or lose state.
                adapter = _S3Operations(self.storage.client, self.storage.bucket,
                                        self.storage._sse, deadline)
                return getattr(adapter, operation)(*args)
            if operation == "put":
                key, data, mime = args
                result = self.storage.put(key, data, content_type=mime)
                if not isinstance(result, StoredObject) or result.byte_size != len(data):
                    raise StorageError("storage_put_uncertain")
                _check_deadline(deadline)
                return result
            if operation in ("read", "read_original"):
                key, version = args
                if version is not None:
                    raise StorageError("storage_version_unsupported")
                return _read_body(self.storage.open(key), deadline,
                                  MAX_ORIGINAL_BYTES if operation == "read_original" else MAX_ASSET_BYTES)
            key, = args
            self.storage.delete(key)
            _check_deadline(deadline)
            try:
                body = self.storage.open(key)
            except StorageError as exc:
                # L16 local's specific missing signal is reliable; unknown
                # network adapters must not masquerade as a local/fake backend.
                if exc.code == "storage_not_found" and exc.__cause__ is None:
                    return None
                raise
            body.close()
            raise StorageError("storage_delete_uncertain")
        except StorageError:
            raise
        except Exception:
            raise StorageError("storage_" + operation + "_failed") from None

    def _isolated_s3(self, operation: str, args: tuple, deadline: float):
        from botocore.config import Config
        from botocore.credentials import RefreshableCredentials

        client = self.storage.client
        # SourceStorage supplies static credentials. Reject refreshable sources
        # instead of allowing a credential refresh to block outside our child.
        credentials = client._request_signer._credentials
        if credentials is None or isinstance(credentials, RefreshableCredentials):
            raise StorageError("storage_configuration")
        credentials = credentials.get_frozen_credentials()
        config = client.meta.config.merge(Config(
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "total_max_attempts": 1},
        ))
        client_args = dict(
            endpoint_url=client.meta.endpoint_url, region_name=client.meta.region_name,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_session_token=credentials.token, config=config,
            verify=client._endpoint.http_session._verify,
        )
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        # Transfer large inputs/results in shared memory, keeping spawn's input
        # pipe and recv() control messages small even for a 2 MiB object.
        capacity = MAX_ORIGINAL_BYTES if operation == "read_original" else MAX_ASSET_BYTES if operation in ("read", "put") else 0
        output = context.RawArray("B", capacity)
        if operation == "put":
            key, data, mime = args
            output[:len(data)] = data
            args = (key, len(data), mime)
        process = context.Process(target=_s3_child, args=(
            sender, output, client_args, self.storage.bucket, dict(self.storage._sse),
            operation, args, deadline), daemon=True)
        started = False
        try:
            _check_deadline(deadline)
            process.start()
            started = True
            sender.close()
            if not receiver.poll(max(0, deadline - time.monotonic())):
                raise StorageError("storage_timeout")
            ok, result = receiver.recv()
            _check_deadline(deadline)
            if not ok:
                raise StorageError(result)
            if operation in ("read", "read_original"):
                if type(result) is not int or not 0 <= result <= capacity:
                    raise StorageError("storage_read_failed")
                return bytes(output[:result])
            return result
        finally:
            receiver.close()
            sender.close()
            if started:
                if process.is_alive():
                    process.terminate()
                process.join(PROCESS_STOP_TIMEOUT_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(PROCESS_STOP_TIMEOUT_SECONDS)
                if process.is_alive():
                    raise StorageError("storage_writer_unresolved")
                process.close()

    def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        _key(key)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ASSET_BYTES:
            raise StorageError("storage_size_invalid")
        if not isinstance(mime, str) or not mime or len(mime) > 255 or any(ord(c) < 32 for c in mime):
            raise StorageError("storage_mime_invalid")
        return self._run("put", key, data, mime)

    def read(self, key: str, version: str | None = None) -> bytes:
        _key(key)
        _version(version)
        return self._run("read", key, version)

    def read_original(self, key: str, version: str | None = None) -> bytes:
        """Only caller-resolved L16 registry references; not a public key API."""
        _key(key)
        _version(version)
        return self._run("read_original", key, version)

    def verify(self, key: str, sha256: str, byte_size: int, version: str | None = None) -> bool:
        _key(key)
        _version(version)
        if (not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256)
                or type(byte_size) is not int or not 0 < byte_size <= MAX_ASSET_BYTES):
            raise StorageError("storage_verification_invalid")
        try:
            data = self.read(key, version)
        except StorageError as exc:
            if (exc.code in ("storage_not_found", "storage_size_exceeded", "storage_version_mismatch")
                    and exc.__cause__ is None):
                return False
            raise
        return len(data) == byte_size and hmac.compare_digest(hashlib.sha256(data).hexdigest(), sha256.lower())

    def erase(self, key: str) -> None:
        _key(key)
        self._run("erase", key)

    def reconcile_write(self, key, sha256, byte_size):
        _key(key)
        if self.backend_name != 's3':
            return self.verify(key, sha256, byte_size)
        if (type(byte_size) is not int or not 0 < byte_size <= MAX_ASSET_BYTES
                or not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256)):
            raise StorageError('storage_verification_invalid')
        return self._run('reconcile_write', key, sha256, byte_size)
