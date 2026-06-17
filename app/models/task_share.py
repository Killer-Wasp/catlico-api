import uuid

from sqlmodel import Field

from app.models.common import CreatedMixin


class TaskShare(CreatedMixin, table=True):
    __tablename__ = "task_share"

    task_id: uuid.UUID = Field(foreign_key="task.id", primary_key=True, ondelete="CASCADE")
    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
