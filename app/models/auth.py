import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_token"

    token: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


class ForgotPasswordRequest(SQLModel):
    email: str


class ResetPasswordRequest(SQLModel):
    token: str
    new_password: str
