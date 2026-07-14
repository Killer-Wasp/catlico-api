import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


# Cap stored User-Agent length: real UA strings are well under this, but the
# header is attacker-controlled so we truncate defensively before persisting.
USER_AGENT_MAX_LENGTH = 400
# IPv6 addresses are at most 45 chars (IPv4-mapped form). The IP can arrive via
# an attacker-controlled X-Forwarded-For header, so it gets the same defensive
# cap as the UA.
IP_ADDRESS_MAX_LENGTH = 45


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_token"

    token: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Device metadata captured at issuance, for the session list. Nullable so
    # tokens issued before this feature (and any non-HTTP path) stay valid.
    user_agent: str | None = Field(default=None, max_length=USER_AGENT_MAX_LENGTH)
    ip_address: str | None = Field(default=None, max_length=IP_ADDRESS_MAX_LENGTH)


class PasswordResetToken(SQLModel, table=True):
    """Single-use token for password reset flow (G6)."""
    __tablename__ = "password_reset_token"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    token_hash: str = Field(index=True)  # sha256 of the emailed token
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- API I/O ---


class SessionPublic(SQLModel):
    """A refresh-token session as exposed by GET /auth/sessions."""

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None
    is_current: bool


class ForgotPasswordRequest(SQLModel):
    email: str


class ResetPasswordRequest(SQLModel):
    token: str
    new_password: str
