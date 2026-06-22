import uuid
from datetime import datetime

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
    user_id: uuid.UUID
    role_id: uuid.UUID


class OrganisationMemberPublic(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    organisation_id: str
    role_id: uuid.UUID
    created_at: datetime
    #: The member's email, joined from User — lets clients show/mention a member
    #: without an extra lookup per row.
    email: str


class OrganisationMemberUpdate(SQLModel):
    role_id: uuid.UUID
