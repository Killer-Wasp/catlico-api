"""S3 blob-storage coverage: a real s3fs round-trip against an in-process S3
server, plus the Settings backend-selection logic.

s3fs talks to S3 via aiobotocore, which moto's in-process `mock_aws` patches
unreliably; a real `moto.server.ThreadedMotoServer` (from the `moto[s3,server]`
dev dep) speaks HTTP so s3fs hits it exactly like a live endpoint. `port=0`
binds a free port, so each test's endpoint is unique and s3fs's global instance
cache never collides across tests — no manual cache clearing needed.
"""
import boto3
import pytest
from moto.server import ThreadedMotoServer

from app.core.configs import Settings
from app.core.storage import BlobStorage

_BUCKET = "catlico-test"
_DB = {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}


@pytest.fixture
def s3_endpoint():
    """Start a throwaway in-process S3 server and pre-create the bucket (s3fs
    won't auto-create it), yielding the endpoint URL."""
    server = ThreadedMotoServer(port=0)
    server.start()
    _, port = server.get_host_and_port()
    endpoint = f"http://127.0.0.1:{port}"
    boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    ).create_bucket(Bucket=_BUCKET)
    try:
        yield endpoint
    finally:
        server.stop()


async def test_s3_round_trip(s3_endpoint):
    """Full lifecycle against the real s3fs code path: exists/put/idempotency/
    stream/delete."""
    storage = BlobStorage(
        "s3",
        _BUCKET,
        {
            "key": "key",
            "secret": "secret",
            "client_kwargs": {
                "endpoint_url": s3_endpoint,
                "region_name": "us-east-1",
            },
        },
    )
    sha = "a" * 64
    payload = b"hello s3 blob"

    assert await storage.exists(sha) is False

    await storage.put(sha, payload)
    assert await storage.exists(sha) is True

    # Idempotent: a second put of the same content-addressed blob is a no-op.
    await storage.put(sha, payload)
    assert await storage.exists(sha) is True

    chunks = [chunk async for chunk in storage.stream(sha)]
    assert b"".join(chunks) == payload

    await storage.delete(sha)
    assert await storage.exists(sha) is False


def test_s3_endpoint_selects_s3_protocol():
    """A set S3_ENDPOINT_URL flips the effective protocol to s3 and populates
    fsspec storage_options, regardless of the (default) STORAGE_PROTOCOL."""
    s = Settings(
        _env_file=None,
        S3_ENDPOINT_URL="http://localhost:8333",
        S3_ACCESS_KEY="catlico",
        S3_SECRET_KEY="catlico-secret",
        **_DB,
    )
    assert s.effective_storage_protocol == "s3"
    assert s.storage_options == {
        "client_kwargs": {"endpoint_url": "http://localhost:8333"},
        "key": "catlico",
        "secret": "catlico-secret",
    }


def test_no_endpoint_defaults_to_local():
    """With no S3_ENDPOINT_URL, the effective protocol falls back to the default
    local backend and storage_options is empty."""
    s = Settings(_env_file=None, **_DB)
    assert s.effective_storage_protocol == "local"
    assert s.storage_options == {}
