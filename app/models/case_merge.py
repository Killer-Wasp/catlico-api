from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import CreatedMixin


class CaseMerge(CreatedMixin, table=True):
    """Self-M2M merge lineage: a source case was merged into a target (new) case.

    Single source of truth for merge lineage — both directions (target←sources,
    source→target) are derivable from this one table, so merge never touches
    Case.duplicate_of_case_id (that belongs to the separate manual mark-as-duplicate
    feature). See docs/case-merge-design.md.
    """

    __tablename__ = "case_merge"

    source_case_id: int = Field(
        foreign_key="case_.id", primary_key=True, ondelete="CASCADE"
    )
    target_case_id: int = Field(
        foreign_key="case_.id", primary_key=True, index=True, ondelete="CASCADE"
    )


class CaseMergePublic(SQLModel):
    source_case_id: int
    target_case_id: int
    created_at: datetime
