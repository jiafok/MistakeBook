"""Storage abstraction: local directory, NAS mount (same code path), or MinIO."""

from __future__ import annotations

import io
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from .config import settings


class StorageError(RuntimeError):
    pass


class StorageBackend:
    def save(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    def read(self, key: str) -> bytes:
        raise NotImplementedError

    def open_stream(self, key: str) -> BinaryIO:
        return io.BytesIO(self.read(key))

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorage(StorageBackend):
    """Used for local disk and for NAS shared folders (a mounted path)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            raise StorageError("invalid storage key")
        target = (self.root / safe).resolve()
        if not str(target).startswith(str(self.root)):
            raise StorageError("path escapes storage root")
        return target

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        shutil.move(str(tmp), str(path))

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"missing object: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class MinioStorage(StorageBackend):
    """MinIO / S3 compatible via the minio client, imported lazily."""

    def __init__(self) -> None:
        try:
            from minio import Minio  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise StorageError("minio package is not installed; pip install minio") from exc
        if not settings.minio_endpoint:
            raise StorageError("MINIO_ENDPOINT is not configured")
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket = settings.minio_bucket
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def save(self, key: str, data: bytes) -> None:
        self.client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data), content_type="application/octet-stream"
        )

    def read(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        backend = settings.storage_backend.lower()
        if backend == "minio":
            _backend = MinioStorage()
        else:
            _backend = LocalStorage(settings.storage_dir)
    return _backend
