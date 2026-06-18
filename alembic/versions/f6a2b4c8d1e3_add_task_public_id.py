"""add task public id

Revision ID: f6a2b4c8d1e3
Revises: e1f2a3b4c5d6
Create Date: 2026-06-18 14:45:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a2b4c8d1e3"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("public_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )

    op.execute(
        """
        UPDATE task
        SET public_id = 'T-' || case_id || '-' || (
            SELECT COUNT(*)
            FROM task AS numbered_task
            WHERE numbered_task.case_id = task.case_id
              AND (
                numbered_task.created_at < task.created_at
                OR (
                  numbered_task.created_at = task.created_at
                  AND CAST(numbered_task.id AS TEXT) <= CAST(task.id AS TEXT)
                )
              )
        )
        """
    )

    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.alter_column(
            "public_id",
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        )
        batch_op.create_index(
            batch_op.f("ix_task_public_id"), ["public_id"], unique=True
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_public_id"))
        batch_op.drop_column("public_id")
