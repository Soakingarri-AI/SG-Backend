"""Blob storage for user uploads, with a swappable backend.

S3 is the intended home, but the production box currently has no AWS
credentials, so the default backend writes to a mounted volume. Both backends
expose the same four operations, and ``STORAGE_BACKEND`` picks between them —
moving to S3 later is a configuration change, not a code change.

Storage keys are generated here and never built from user input: a key is
``<prefix>/<user id>/<random hex><extension>``, so a crafted filename cannot
escape the upload directory or collide with another user's file.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import settings

# Conservative: letters, digits, dot, underscore and hyphen only.
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


class StorageError(RuntimeError):
    """Raised when the backend cannot store or retrieve an object."""


def build_key(user_id: str, filename: str, prefix: str = "uploads") -> str:
    """A collision-free, traversal-proof key that preserves the extension."""
    suffix = Path(filename).suffix.lower()
    if not _SAFE_EXTENSION.match(suffix):
        suffix = ""
    return f"{prefix}/{user_id}/{uuid.uuid4().hex}{suffix}"


class StorageBackend(Protocol):
    def save(self, key: str, data: bytes, content_type: str) -> None: ...
    def load(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    """Writes under ``UPLOAD_DIR``; needs a mounted volume to survive restarts."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Defence in depth: keys are generated, but never serve a path that
        # resolved outside the upload root.
        if not path.is_relative_to(self.root.resolve()):
            raise StorageError("Refusing to access a path outside the upload root")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"Object not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)


class S3Storage:
    """Delegates to the shared S3 service. Requires working AWS credentials."""

    def save(self, key: str, data: bytes, content_type: str) -> None:
        from app.services.s3_service import s3_service

        try:
            s3_service.put_bytes(key, data, content_type)
        except Exception as exc:
            raise StorageError(str(exc)) from exc

    def load(self, key: str) -> bytes:
        from app.services.s3_service import s3_service

        try:
            obj = s3_service._client.get_object(Bucket=s3_service.bucket, Key=key)
            return obj["Body"].read()
        except Exception as exc:
            raise StorageError(str(exc)) from exc

    def delete(self, key: str) -> None:
        from app.services.s3_service import s3_service

        try:
            s3_service._client.delete_object(Bucket=s3_service.bucket, Key=key)
        except Exception as exc:
            raise StorageError(str(exc)) from exc


def _build_backend() -> StorageBackend:
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(settings.UPLOAD_DIR)


storage = _build_backend()
