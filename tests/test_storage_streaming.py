"""§6.5.6: save_upload streams uploads to a temp blob with a rolling sha256 and
enforces the size cap on the stream, without ever fully buffering the bytes."""
import hashlib
import io

import pytest

from app.core.storage import BlobStorage, UploadTooLarge, save_upload


class _FakeUpload:
    """Minimal stand-in exercising exactly what save_upload consumes: an async
    chunked ``read`` and a ``content_type``."""

    def __init__(self, data: bytes, content_type: str = "application/octet-stream"):
        self._buf = io.BytesIO(data)
        self.content_type = content_type

    async def read(self, n: int) -> bytes:
        return self._buf.read(n)


async def test_streaming_upload_hashes_and_stores(tmp_path):
    storage = BlobStorage("local", str(tmp_path / "root"))
    # Larger than the 1 MiB chunk so multiple read()s (and multiple writes) happen.
    data = b"catlico-blob-" * 200_000
    expected_sha = hashlib.sha256(data).hexdigest()

    sha, size, content_type = await save_upload(storage, _FakeUpload(data, "text/plain"), 100 * 1024 * 1024)

    assert sha == expected_sha
    assert size == len(data)
    assert content_type == "text/plain"
    assert await storage.exists(sha) is True
    chunks = [c async for c in storage.stream(sha)]
    assert b"".join(chunks) == data
    # The staging temp file was promoted, not left behind.
    blobs = await storage.iter_blobs()
    assert [b.sha256 for b in blobs] == [expected_sha]


async def test_streaming_upload_enforces_cap_and_cleans_up(tmp_path):
    storage = BlobStorage("local", str(tmp_path / "root"))
    data = b"x" * (3 * 1024 * 1024)  # 3 MiB

    with pytest.raises(UploadTooLarge):
        await save_upload(storage, _FakeUpload(data), max_bytes=1 * 1024 * 1024)

    # No blob landed, and the aborted temp file was discarded (nothing over the cap
    # remains on disk).
    assert (await storage.iter_blobs()) == []
    tmp_dir = tmp_path / "root" / "tmp"
    assert not tmp_dir.exists() or not any(tmp_dir.iterdir())


async def test_streaming_upload_idempotent_dedupe(tmp_path):
    """A second upload of identical bytes dedupes to the same content-addressed path."""
    storage = BlobStorage("local", str(tmp_path / "root"))
    data = b"dedupe-me"

    sha1, _, _ = await save_upload(storage, _FakeUpload(data), 100 * 1024 * 1024)
    sha2, _, _ = await save_upload(storage, _FakeUpload(data), 100 * 1024 * 1024)

    assert sha1 == sha2
    blobs = await storage.iter_blobs()
    assert [b.sha256 for b in blobs] == [sha1]
