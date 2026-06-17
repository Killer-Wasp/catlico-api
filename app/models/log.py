import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


class Log(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "log"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(foreign_key="task.id", index=True, ondelete="CASCADE")
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )
    message: str
    # Analyst-set time the event actually happened (may be backdated). Distinct from
    # created_at, which is the immutable time the entry was typed. Defaults to created_at.
    occurred_at: datetime | None = Field(default=None)


class LogCreate(SQLModel):
    message: str = Field(description=MARKDOWN_NOTE)
    occurred_at: datetime | None = None


class LogPublic(SQLModel):
    id: uuid.UUID
    task_id: uuid.UUID
    organisation_id: str
    message: str = Field(description=MARKDOWN_NOTE)
    occurred_at: datetime | None
    created_at: datetime
    created_by: str
    updated_at: datetime | None


class LogUpdate(SQLModel):
    message: str | None = Field(default=None, description=MARKDOWN_NOTE)
    occurred_at: datetime | None = None
