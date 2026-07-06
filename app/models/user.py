import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True)
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)
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

    @property
    def has_avatar(self) -> bool:
        return self.avatar_attachment_id is not None


class UserCreate(SQLModel):
    email: EmailStr
    password: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_superadmin: bool = False


class UserPublic(SQLModel):
    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    is_active: bool
    is_superadmin: bool
    has_avatar: bool
    created_at: datetime
    last_login_at: datetime | None


class UserUpdate(SQLModel):
    email: EmailStr | None = None
    password: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None


class UserMeUpdate(SQLModel):
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    current_password: str | None = None
    new_password: str | None = None
