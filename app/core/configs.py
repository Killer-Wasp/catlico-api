import secrets
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    PostgresDsn,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    # PostgreSQL connection — required. Either set DATABASE_URL directly, or set
    # POSTGRES_SERVER + POSTGRES_USER (+ the rest). Postgres is the only supported
    # engine; dev, test, and prod all run on it for parity.
    POSTGRES_SERVER: str | None = None
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    # Explicit override — takes precedence over everything else
    DATABASE_URL: str | None = None
    # Echo every SQL statement to the logger. Off by default; opt in for local
    # debugging. Never enable in production — it logs full statements/params.
    DB_ECHO: bool = False

    # Security
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # Access tokens are short-lived because they embed org membership + the
    # superadmin flag; a short life caps how long a stale permission set stays
    # usable. Clients silently renew via POST /auth/refresh, which re-reads
    # membership from the DB. Refresh tokens are random UUIDs persisted in DB.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days

    # Default admin seeding
    DEFAULT_ADMIN_EMAIL: str = "admin@example.com"
    DEFAULT_ADMIN_PASSWORD: str = "changeme"

    # Connector / analyzer engine.
    # Shared secret the catlico-connector-engine service presents (Bearer) to register and
    # pull work. Unset ⇒ analyzer endpoints reject all callers. Per-org API keys
    # replace this later behind the same header.
    ANALYZER_SHARED_SECRET: str | None = None
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt connector secrets at
    # rest. Required at startup.
    SECRET_ENCRYPTION_KEY: str | None = None
    # Dedup window: an enrich request reuses a successful job younger than this
    # instead of re-dispatching. force_refresh overrides.
    CONNECTOR_CACHE_TTL_SECONDS: int = 86400
    # How long a claimed job stays leased before it can be re-handed to another
    # analyzer poll.
    ANALYZER_LEASE_SECONDS: int = 300
    # Give up after this many lease attempts and mark the job failed.
    ANALYZER_MAX_ATTEMPTS: int = 3

    # Blob storage for file attachments / file observables.
    # Backed by fsspec, so the same code targets local FS, S3 (incl. SeaweedFS/MinIO),
    # GCS, or Azure — just install the matching fsspec adapter and set STORAGE_PROTOCOL.
    STORAGE_PROTOCOL: Literal["local", "s3", "gcs", "az", "abfs"] = "local"
    # Root container for blobs: a directory for `local`, a bucket (optionally
    # bucket/prefix) for object stores.
    STORAGE_ROOT: str = "./var/blobs"
    MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024  # 100 MB
    # S3 / SeaweedFS knobs (used when STORAGE_PROTOCOL=s3). GCS/Azure read ambient
    # credentials (GOOGLE_APPLICATION_CREDENTIALS / AZURE_STORAGE_CONNECTION_STRING).
    S3_ENDPOINT_URL: str | None = None  # e.g. http://seaweedfs:8333
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_options(self) -> dict[str, Any]:
        """fsspec storage_options for the configured protocol."""
        if self.STORAGE_PROTOCOL == "s3":
            opts: dict[str, Any] = {}
            if self.S3_ENDPOINT_URL:
                opts["client_kwargs"] = {"endpoint_url": self.S3_ENDPOINT_URL}
            if self.S3_ACCESS_KEY:
                opts["key"] = self.S3_ACCESS_KEY
            if self.S3_SECRET_KEY:
                opts["secret"] = self.S3_SECRET_KEY
            return opts
        return {}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.POSTGRES_SERVER and self.POSTGRES_USER:
            return str(
                PostgresDsn.build(
                    scheme="postgresql+asyncpg",
                    username=self.POSTGRES_USER,
                    password=self.POSTGRES_PASSWORD,
                    host=self.POSTGRES_SERVER,
                    port=self.POSTGRES_PORT,
                    path=self.POSTGRES_DB,
                )
            )
        raise ValueError(
            "No database configured. Set DATABASE_URL, or POSTGRES_SERVER + "
            "POSTGRES_USER (e.g. run `docker compose up -d db`). Postgres is required."
        )


settings = Settings()
