"""add connector max_runtime_seconds

Revision ID: a1b2c3d4e5f6
Revises: f6a2b4c8d1e3
Create Date: 2026-06-20 09:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f6a2b4c8d1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("connector", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_runtime_seconds",
                sa.Integer(),
                nullable=False,
                server_default="60",
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("connector", schema=None) as batch_op:
        batch_op.drop_column("max_runtime_seconds")
