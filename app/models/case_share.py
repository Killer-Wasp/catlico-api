import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class CaseShare(TimestampMixin, table=True):
    __tablename__ = "case_share"

    case_id: int = Field(foreign_key="case_.id", primary_key=True, ondelete="CASCADE")
    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    role_id: uuid.UUID = Field(foreign_key="role.id", ondelete="RESTRICT")
    is_owner: bool = Field(default=False)


class CaseSharePublic(SQLModel):
    case_id: int
    organisation_id: str
    role_id: uuid.UUID
    is_owner: bool
    created_at: datetime
