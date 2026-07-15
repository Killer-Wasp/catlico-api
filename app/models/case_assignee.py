import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import utcnow


class CaseAssignee(SQLModel, table=True):
    """Collaborator (secondary assignee) on a case. The primary owner stays on
    ``Case.assignee_id``; this join table holds the additional collaborators. The
    composite PK (case_id, user_id) enforces the unique pair. The row is deleted
    when either the case or the user is deleted (CASCADE)."""

    __tablename__ = "case_assignee"

    case_id: int = Field(foreign_key="case_.id", primary_key=True, ondelete="CASCADE")
    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    created_at: datetime = Field(default_factory=utcnow)
