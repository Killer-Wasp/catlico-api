"""Blob storage backed by fsspec.

One abstraction over local FS, S3 (incl. SeaweedFS/MinIO), GCS and Azure. Blobs are
content-addressed by SHA-256, so the key is the hash and identical bytes dedupe.
fsspec calls are synchronous; we offload them to a worker thread so the event loop
isn't blocked.
"""
import hashlib
from collections.abc import AsyncIterator

import anyio
import fsspec
from fastapi import UploadFile

from app.core.configs import settings

_CHUNK = 1024 * 1024  # 1 MiB


class UploadTooLarge(Exception):
    """Raised when an upload exceeds MAX_UPLOAD_BYTES."""


class BlobStorage:
    def __init__(self, protocol: str, root: str, storage_options: dict | None = None):
        self.protocol = protocol
        self.root = root.rstrip("/")
        self.fs = fsspec.filesystem(
            "file" if protocol == "local" else protocol, **(storage_options or {})
        )

    def _path(self, sha256: str) -> str:
        # Shard by the first two hex chars to avoid huge flat directories.
        return f"{self.root}/blobs/{sha256[:2]}/{sha256}"

    async def exists(self, sha256: str) -> bool:
        return await anyio.to_thread.run_sync(self.fs.exists, self._path(sha256))

    async def put(self, sha256: str, data: bytes) -> None:
        """Idempotent: storing an already-present blob is a no-op (content-addressed)."""
        path = self._path(sha256)

        def _write() -> None:
            if self.fs.exists(path):
                return
            parent = path.rsplit("/", 1)[0]
            self.fs.makedirs(parent, exist_ok=True)
            with self.fs.open(path, "wb") as f:
                f.write(data)

        await anyio.to_thread.run_sync(_write)

    async def stream(self, sha256: str) -> AsyncIterator[bytes]:
        """Yield the blob in chunks, reading from the backend on a worker thread."""
        path = self._path(sha256)
        handle = await anyio.to_thread.run_sync(lambda: self.fs.open(path, "rb"))
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(handle.read, _CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await anyio.to_thread.run_sync(handle.close)

    async def delete(self, sha256: str) -> None:
        path = self._path(sha256)

        def _rm() -> None:
            if self.fs.exists(path):
                self.fs.rm_file(path)

        await anyio.to_thread.run_sync(_rm)


async def save_upload(
    storage: BlobStorage, upload: UploadFile, max_bytes: int
) -> tuple[str, int, str]:
    """Stream an upload, hashing as we go and enforcing the size cap, then store it
    content-addressed. Returns (sha256, size, content_type). The blob is held in memory
    up to the cap before writing — fine for the current 100 MB ceiling."""
    hasher = hashlib.sha256()
    size = 0
    buf = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise UploadTooLarge()
        hasher.update(chunk)
        buf.extend(chunk)
    sha256 = hasher.hexdigest()
    await storage.put(sha256, bytes(buf))
    return sha256, size, upload.content_type or "application/octet-stream"


_storage: BlobStorage | None = None


def get_storage() -> BlobStorage:
    """FastAPI dependency. Cached process-wide; the fsspec filesystem is reused."""
    global _storage
    if _storage is None:
        _storage = BlobStorage(
            settings.STORAGE_PROTOCOL,
            settings.STORAGE_ROOT,
            settings.storage_options,
        )
    return _storage
