import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True)
    is_active: bool = True
    is_superadmin: bool = False


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = Field(default=None)
    last_login_at: datetime | None = Field(default=None)


class UserCreate(SQLModel):
    email: EmailStr
    password: str | None = None
    is_superadmin: bool = False


class UserPublic(SQLModel):
    id: uuid.UUID
    email: EmailStr
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    last_login_at: datetime | None


class UserUpdate(SQLModel):
    email: EmailStr | None = None
    password: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None


class UserMeUpdate(SQLModel):
    email: EmailStr | None = None
    current_password: str | None = None
    new_password: str | None = None
