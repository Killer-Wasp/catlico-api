import secrets
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    PostgresDsn,
    computed_field,
    model_validator,
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
            self.FRONTEND_HOST.rstrip("/")
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
    # Mark auth cookies `Secure` (HTTPS-only). Set false for plain-http local dev.
    COOKIE_SECURE: bool = True

    # Default admin seeding
    DEFAULT_ADMIN_EMAIL: str = "admin@example.com"
    DEFAULT_ADMIN_PASSWORD: str = "changeme"
    DEFAULT_ADMIN_FIRST_NAME: str = "Catlico"
    DEFAULT_ADMIN_LAST_NAME: str = "Administrator"

    # Password reset delivery. If SMTP_HOST is unset, reset tokens are stored but
    # not delivered (same public response, no raw-token logging).
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str = "no-reply@catlico.local"
    SMTP_USE_TLS: bool = True
    PASSWORD_RESET_PATH: str = "/reset-password"
    PASSWORD_RESET_THROTTLE_SECONDS: int = 300

    # Plugin runner engine.
    # Legacy shared secret kept only as a deprecated config knob while the
    # enrollment flow rolls out. Internal plugin-runner endpoints authenticate
    # with per-runner machine credentials.
    PLUGIN_RUNNER_SHARED_SECRET: str | None = None
    PLUGIN_RUNNER_ENROLLMENT_TOKEN_TTL_SECONDS: int = 900
    # Plugin runtime engine.
    # Short-lived tokens minted per PluginRun for plugin-runtime API access.
    PLUGIN_RUNTIME_TOKEN_TTL_SECONDS: int = 900
    PLUGIN_RUNTIME_FILE_MAX_BYTES: int = 25 * 1024 * 1024
    PLUGIN_RUNTIME_FILE_MAX_COUNT: int = 20
    PLUGIN_RUNTIME_FILE_MAX_TOTAL_BYTES: int = 100 * 1024 * 1024
    # Plugin maintenance sweep (reaper, offline detection, retention, rollup).
    PLUGIN_MAINTENANCE_INTERVAL_SECONDS: int = 30
    PLUGIN_RUN_REAP_GRACE_SECONDS: int = 60
    PLUGIN_RUN_DEFAULT_TIMEOUT_SECONDS: int = 60
    PLUGIN_HEARTBEAT_INTERVAL_SECONDS: int = 30
    PLUGIN_RUNNER_OFFLINE_MISSED_HEARTBEATS: int = 3
    PLUGIN_RUN_RETENTION_DAYS: int = 30
    PLUGIN_DELIVERY_RETENTION_DAYS: int = 7
    PLUGIN_RESULT_RETENTION_DAYS: int = 90
    # Config circuit breaker: consecutive `config`-kind failures that auto-suspend
    # an org's plugin. A passing config test (or any successful run) resets it.
    PLUGIN_CONFIG_FAILURE_THRESHOLD: int = 3
    # Max total attempts for a run that keeps failing with a `transient` error
    # before we give up. Semantic: a run may be ATTEMPTED at most this many times.
    # Auto-retry fires while `run.attempt < PLUGIN_TRANSIENT_MAX_ATTEMPTS`, so with
    # the default 3 attempts 1 and 2 re-queue and a transient failure on attempt 3
    # stays terminal. Only `transient` failures retry; other outcomes never do.
    PLUGIN_TRANSIENT_MAX_ATTEMPTS: int = 3
    # Event push to runners (delivery retry policy).
    PLUGIN_PUSH_INTERVAL_SECONDS: int = 5
    PLUGIN_PUSH_BACKOFF_BASE_SECONDS: int = 30
    PLUGIN_PUSH_BACKOFF_CAP_SECONDS: int = 900
    PLUGIN_PUSH_MAX_AGE_SECONDS: int = 86400
    PLUGIN_PUSH_TIMEOUT_SECONDS: float = 10.0
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt connector secrets at
    # rest. Required at startup.
    SECRET_ENCRYPTION_KEY: str | None = None

    # MITRE CTI enterprise-attack STIX bundle (ATT&CK catalog import).
    ATTACK_BUNDLE_URL: str = (
        "https://raw.githubusercontent.com/mitre/cti/master/"
        "enterprise-attack/enterprise-attack.json"
    )

    # Function runner mode.
    # - "stub": test stub marks every run successful (no sandbox). Local/test only.
    # - "disabled": reject queued runs with a clear status. Production default.
    FUNCTION_RUNNER_MODE: Literal["disabled", "stub"] = "disabled"

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

    @model_validator(mode="after")
    def _require_explicit_secret_key_in_production(self) -> "Settings":
        """Refuse to boot production on an unset or weak SECRET_KEY.

        The default is a per-process random value — fine for local dev, but in
        production it silently invalidates every JWT on restart and breaks
        multi-instance deployments; and an operator pasting a placeholder would
        quietly run with a guessable signing key. ``model_fields_set`` only
        contains explicitly provided fields, so the generated default is
        detectable.
        """
        if self.ENVIRONMENT != "production":
            return self
        if "SECRET_KEY" not in self.model_fields_set:
            raise ValueError(
                "SECRET_KEY must be explicitly set in production "
                "(e.g. `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`)."
            )
        if len(self.SECRET_KEY) < 32 or self.SECRET_KEY.lower() in {
            "changeme", "changethis", "secret", "secret_key", "default",
        }:
            raise ValueError(
                "SECRET_KEY is too weak for production: use a random value of "
                "at least 32 characters."
            )
        return self

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
