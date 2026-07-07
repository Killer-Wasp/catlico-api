import uuid
from datetime import datetime

from pydantic import EmailStr
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class OrganisationMember(TimestampMixin, table=True):
    __tablename__ = "organisation_member"
    __table_args__ = (UniqueConstraint("user_id", "organisation_id", name="uq_member_user_org"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    role_id: uuid.UUID = Field(foreign_key="role.id", ondelete="RESTRICT")


class OrganisationMemberCreate(SQLModel):
    role_id: uuid.UUID
    user_id: uuid.UUID | None = None
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None


class OrganisationMemberPublic(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    organisation_id: str
    role_id: uuid.UUID
    created_at: datetime
    #: The member's identity, joined from User — lets clients show/mention a member
    #: (name + avatar) without an extra lookup per row.
    email: str
    first_name: str | None = None
    last_name: str | None = None
    #: Whether the user has uploaded a profile picture; clients fetch it from
    #: GET /users/{user_id}/avatar and fall back to initials when false.
    has_avatar: bool = False


class OrganisationMemberUpdate(SQLModel):
    role_id: uuid.UUID
