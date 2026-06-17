from enum import Enum

from sqlmodel import Field

from app.models.common import CreatedMixin


class FlagEntityType(str, Enum):
    case = "case"
    task = "task"
    alert = "alert"


class Flag(CreatedMixin, table=True):
    """Per-org salience marker. Polymorphic over cases/tasks (and future entities).

    Presence of a row = flagged by that organisation; absence = not flagged.
    `entity_id` is stored as a string so it can hold both integer case numbers and
    UUID task ids. This sidesteps the owner-org-has-no-task_share problem: every org
    that can see an entity can flag it, regardless of how visibility is granted.
    """

    __tablename__ = "flag"

    entity_type: FlagEntityType = Field(primary_key=True)
    entity_id: str = Field(primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
