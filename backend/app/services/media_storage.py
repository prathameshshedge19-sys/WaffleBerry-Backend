"""Vendor-neutral private binary storage boundary for L16.

The local adapter is intended for tests and debug only. The S3 adapter is
configured entirely from environment settings and requires SSE-C material; no
secret is ever persisted in the database or returned by an API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import Settings, get_settings


class StorageError(RuntimeError):
    def __init__(self, code: str):
        super().__init__("Media storage operation failed.")
        self.code = code


@dataclass(frozen=True)
class StoredObject:
    byte_size: int
    etag: str | None = None
    version: str | None = None


class SourceStorage(Protocol):
    backend_name: str
    encryption_key_id: str | None

    def put(self, object_key: str, data: bytes, *, content_type: str) -> StoredObject: ...
    def open(self, object_key: str) -> BinaryIO: ...
    def exists(self, object_key: str) -> bool: ...
    def delete(self, object_key: str, *, version: str | None = None) -> None: ...


def _safe_key(object_key: str) -> str:
    if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
        raise StorageError("storage_key_invalid")
    return object_key.replace("\\", "/")


class LocalSourceStorage:
    backend_name = "local"
    encryption_key_id = None

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        key = _safe_key(object_key)
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("storage_key_invalid")
        return path

    def put(self, object_key: str, data: bytes, *, content_type: str) -> StoredObject:
        path = self._path(object_key)
        if path.exists():
            raise StorageError("storage_object_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            raise StorageError("storage_object_exists") from None
        return StoredObject(len(data))

    def open(self, object_key: str) -> BinaryIO:
        try:
            return self._path(object_key).open("rb")
        except FileNotFoundError:
            raise StorageError("storage_not_found") from None

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).is_file()

    def delete(self, object_key: str, *, version: str | None = None) -> None:
        try:
            self._path(object_key).unlink()
        except FileNotFoundError:
            return


class S3SourceStorage:
    backend_name = "s3"

    def __init__(self, settings: Settings):
        required = (settings.media_s3_endpoint_url, settings.media_s3_bucket,
                    settings.media_s3_access_key_id, settings.media_s3_secret_access_key,
                    settings.media_s3_sse_customer_key, settings.media_s3_sse_customer_key_id)
        if not all(required):
            raise StorageError("storage_configuration")
        try:
            import boto3
        except ImportError:
            raise StorageError("storage_dependency") from None
        self.bucket = settings.media_s3_bucket
        self.encryption_key_id = settings.media_s3_sse_customer_key_id
        self._sse = {
            "SSECustomerAlgorithm": "AES256",
            "SSECustomerKey": settings.media_s3_sse_customer_key,
        }
        self.client = boto3.client(
            "s3", endpoint_url=settings.media_s3_endpoint_url,
            region_name=settings.media_s3_region,
            aws_access_key_id=settings.media_s3_access_key_id,
            aws_secret_access_key=settings.media_s3_secret_access_key,
        )

    def put(self, object_key: str, data: bytes, *, content_type: str) -> StoredObject:
        try:
            response = self.client.put_object(Bucket=self.bucket, Key=_safe_key(object_key), Body=data, ContentType=content_type, **self._sse)
        except Exception as exc:
            raise StorageError("storage_put_failed") from exc
        return StoredObject(len(data), response.get("ETag"), response.get("VersionId"))

    def open(self, object_key: str) -> BinaryIO:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=_safe_key(object_key), **self._sse)
        except Exception as exc:
            raise StorageError("storage_not_found") from exc
        return response["Body"]

    def exists(self, object_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(object_key), **self._sse)
            return True
        except Exception:
            return False

    def delete(self, object_key: str, *, version: str | None = None) -> None:
        kwargs = {"Bucket": self.bucket, "Key": _safe_key(object_key)}
        if version:
            kwargs["VersionId"] = version
        try:
            self.client.delete_object(**kwargs)
        except Exception as exc:
            raise StorageError("storage_delete_failed") from exc


def get_source_storage(settings: Settings | None = None) -> SourceStorage:
    settings = settings or get_settings()
    if settings.media_storage_backend == "s3":
        return S3SourceStorage(settings)
    return LocalSourceStorage(settings.media_local_storage_path)
