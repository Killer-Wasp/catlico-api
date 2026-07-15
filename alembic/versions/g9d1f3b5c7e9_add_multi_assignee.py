"""add multi-assignee join tables (cases + tasks)

Revision ID: g9d1f3b5c7e9
Revises: f8b0d2e4c6a9
Create Date: 2026-07-16 09:00:00.000000

Phase 4 §4.2: multi-assignee for cases + tasks. `Case.assignee_id` /
`Task.assignee_id` stay as the primary owner; these join tables hold the
additional collaborators (unique pair per entity+user, row deleted on entity or
user delete). Alerts stay single-assignee. See plans/phase-4-parity.md §4.2.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g9d1f3b5c7e9"
down_revision: Union[str, Sequence[str], None] = "f8b0d2e4c6a9"  # down_revision fixed at merge
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_assignee",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["case_.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id", "user_id"),
    )
    op.create_index(
        op.f("ix_case_assignee_user_id"),
        "case_assignee",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "task_assignee",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id", "task_id", "user_id"),
    )
    op.create_index(
        op.f("ix_task_assignee_user_id"),
        "task_assignee",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_assignee_user_id"), table_name="task_assignee")
    op.drop_table("task_assignee")
    op.drop_index(op.f("ix_case_assignee_user_id"), table_name="case_assignee")
    op.drop_table("case_assignee")
