import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True)
    # Every user must have a name. Enforced non-null at the DB level and
    # non-empty on the input schemas (UserCreate / UserUpdate / UserMeUpdate).
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    is_active: bool = True
    is_superadmin: bool = False


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str | None = Field(default=None)
    # Content-addressed avatar blob (reuses the shared attachment/blob store).
    # None means no profile picture has been uploaded.
    avatar_attachment_id: uuid.UUID | None = Field(
        default=None, foreign_key="attachment.id", ondelete="SET NULL"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = Field(default=None)
    last_login_at: datetime | None = Field(default=None)
    # Local-password brute-force throttle (OSS-core account lockout). Counts
    # consecutive failed password attempts; when it reaches LOGIN_MAX_ATTEMPTS the
    # account is locked until `locked_until` and the counter resets. Both are
    # cleared on a successful login (and by a superadmin re-activating the user).
    failed_login_count: int = Field(default=0, nullable=False)
    locked_until: datetime | None = Field(default=None)
    # Force-reset lever: when True, a successful password login issues no session
    # and instead bounces the user to set a new password (see the login gate in
    # app/api/v1/routes/auth.py and the enterprise MFA/passkey verify paths). It
    # is a PASSWORD-PATH lever only — SSO/OIDC logins are passwordless and exempt.
    # Cleared whenever the user sets a password (set_password / perform_reset).
    must_change_password: bool = Field(default=False, nullable=False)

    @property
    def has_avatar(self) -> bool:
        return self.avatar_attachment_id is not None


class UserCreate(SQLModel):
    # No password: admin-created accounts are always password-less and receive a
    # set-password invite email. The user owns their own credential.
    email: EmailStr
    # Required: a user cannot be created without a name.
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    is_superadmin: bool = False


class UserPublic(SQLModel):
    id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str
    is_active: bool
    is_superadmin: bool
    has_avatar: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None


class UserUpdate(SQLModel):
    # No password: admins cannot set a user's password (self-service only). They
    # can, however, flip must_change_password to force a reset on next login.
    email: EmailStr | None = None
    # Omit to leave unchanged; when provided it must be non-empty (no clearing).
    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None
    is_superadmin: bool | None = None
    must_change_password: bool | None = None


class UserMeUpdate(SQLModel):
    email: EmailStr | None = None
    # Omit to leave unchanged; when provided it must be non-empty (no clearing).
    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = Field(default=None, min_length=1)
    current_password: str | None = None
    new_password: str | None = None
