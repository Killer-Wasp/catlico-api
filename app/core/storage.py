"""Blob storage backed by fsspec.

One abstraction over local FS, S3 (incl. SeaweedFS/MinIO), GCS and Azure. Blobs are
content-addressed by SHA-256, so the key is the hash and identical bytes dedupe.
fsspec calls are synchronous; we offload them to a worker thread so the event loop
isn't blocked.
"""
import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import fsspec
from fastapi import UploadFile

from app.core.configs import settings

_CHUNK = 1024 * 1024  # 1 MiB
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UploadTooLarge(Exception):
    """Raised when an upload exceeds MAX_UPLOAD_BYTES."""


class BlobInfo:
    """A stored blob's identity, size, and last-modified time (for orphan GC)."""

    __slots__ = ("sha256", "size", "mtime")

    def __init__(self, sha256: str, size: int, mtime: datetime | None):
        self.sha256 = sha256
        self.size = size
        self.mtime = mtime


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

    def _tmp_path(self) -> str:
        return f"{self.root}/tmp/{uuid.uuid4().hex}"

    async def open_temp_writer(self, tmp_path: str):
        """Open a fresh temp blob for streaming writes (parent dirs ensured)."""

        def _open():
            parent = tmp_path.rsplit("/", 1)[0]
            self.fs.makedirs(parent, exist_ok=True)
            return self.fs.open(tmp_path, "wb")

        return await anyio.to_thread.run_sync(_open)

    async def finalize_temp(self, tmp_path: str, sha256: str) -> None:
        """Promote a fully-written temp blob to its content-addressed path.

        Idempotent: if the content-addressed blob already exists (dedupe), the temp
        file is dropped instead of overwriting. Otherwise the temp is moved into
        place — a rename on local FS, copy+delete on object stores."""
        path = self._path(sha256)

        def _finalize() -> None:
            if self.fs.exists(path):
                self.fs.rm_file(tmp_path)
                return
            parent = path.rsplit("/", 1)[0]
            self.fs.makedirs(parent, exist_ok=True)
            self.fs.mv(tmp_path, path)

        await anyio.to_thread.run_sync(_finalize)

    async def discard_temp(self, tmp_path: str) -> None:
        """Best-effort cleanup of a temp blob (aborted/oversized upload)."""

        def _rm() -> None:
            try:
                if self.fs.exists(tmp_path):
                    self.fs.rm_file(tmp_path)
            except Exception:
                pass

        await anyio.to_thread.run_sync(_rm)

    async def iter_blobs(self) -> list[BlobInfo]:
        """Enumerate every stored blob under ``blobs/`` with size and mtime.

        Reads the whole listing on a worker thread. Non-blob paths (and the ``tmp/``
        staging area, which lives outside ``blobs/``) are ignored; only basenames
        that are valid sha-256 hex are returned."""
        prefix = f"{self.root}/blobs"

        def _find() -> list[BlobInfo]:
            if not self.fs.exists(prefix):
                return []
            listing = self.fs.find(prefix, detail=True)
            out: list[BlobInfo] = []
            for path, info in listing.items():
                name = path.rsplit("/", 1)[-1]
                if not _SHA256_RE.match(name):
                    continue
                out.append(BlobInfo(name, int(info.get("size") or 0), _mtime_of(info)))
            return out

        return await anyio.to_thread.run_sync(_find)


def _mtime_of(info: dict) -> datetime | None:
    """Normalise an fsspec info dict's modification time to an aware UTC datetime.

    Backends disagree on the key: local FS gives ``mtime`` as an epoch float, s3fs
    gives ``LastModified`` as a datetime. Returns None when neither is present."""
    raw = info.get("mtime") or info.get("LastModified") or info.get("last_modified")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=UTC)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    return None


async def save_upload(
    storage: BlobStorage, upload: UploadFile, max_bytes: int
) -> tuple[str, int, str]:
    """Stream an upload to a temp blob, hashing as we go and enforcing the size cap,
    then promote it content-addressed. Returns (sha256, size, content_type).

    The bytes are never fully buffered in memory: each 1 MiB chunk is written straight
    through to storage. The cap is enforced on the stream — the first chunk that would
    push the running total past ``max_bytes`` aborts (deleting the partial temp blob)
    before it is written, so an oversized upload never lands more than the cap on disk."""
    hasher = hashlib.sha256()
    size = 0
    tmp_path = storage._tmp_path()
    handle = await storage.open_temp_writer(tmp_path)
    try:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLarge()
            hasher.update(chunk)
            await anyio.to_thread.run_sync(handle.write, chunk)
    except BaseException:
        await anyio.to_thread.run_sync(handle.close)
        await storage.discard_temp(tmp_path)
        raise
    await anyio.to_thread.run_sync(handle.close)
    sha256 = hasher.hexdigest()
    await storage.finalize_temp(tmp_path, sha256)
    return sha256, size, upload.content_type or "application/octet-stream"


_storage: BlobStorage | None = None


def get_storage() -> BlobStorage:
    """FastAPI dependency. Cached process-wide; the fsspec filesystem is reused."""
    global _storage
    if _storage is None:
        _storage = BlobStorage(
            settings.effective_storage_protocol,
            settings.STORAGE_ROOT,
            settings.storage_options,
        )
    return _storage
