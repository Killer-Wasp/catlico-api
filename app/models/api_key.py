import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import SoftDeleteMixin, TimestampMixin


class ApiKey(TimestampMixin, SoftDeleteMixin, table=True):
    """An org-scoped programmatic credential. The token is shown to the user
    exactly once at creation; only its sha256 `key_hash` is persisted. `prefix`
    and `last_four` exist purely so the UI can render a masked label
    (`thp_**********3f9a`) without holding the secret. Revoking soft-deletes the
    row. API keys are unscoped: every key grants the full capability set (the
    per-key `scopes` surface was removed)."""

    __tablename__ = "api_key"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    name: str
    prefix: str = Field(default="")
    last_four: str = Field(default="")
    # sha256 hex of the full token — high-entropy, so a plain digest (not bcrypt)
    # is sufficient and lets lookups stay O(1). The plaintext is never stored.
    key_hash: str = Field(index=True)
    last_used_at: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)


class ApiKeyCreate(SQLModel):
    name: str
    expires_at: datetime | None = None


class ApiKeyUpdate(SQLModel):
    name: str | None = None
    expires_at: datetime | None = None


class ApiKeyPublic(SQLModel):
    id: uuid.UUID
    name: str
    prefix: str
    last_four: str
    last_used_at: datetime | None
    expires_at: datetime | None
    organisation_id: str
    created_at: datetime


class ApiKeyCreated(ApiKeyPublic):
    """Returned only from the create endpoint — carries the one-time plaintext
    `key`. Never persisted, never returned by any read."""

    key: str
