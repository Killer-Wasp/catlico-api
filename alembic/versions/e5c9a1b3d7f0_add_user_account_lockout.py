"""add user account lockout fields

Revision ID: e5c9a1b3d7f0
Revises: d7f1a9c3e5b8
Create Date: 2026-07-15 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c9a1b3d7f0"
down_revision: Union[str, Sequence[str], None] = "d7f1a9c3e5b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills existing rows so the NOT NULL constraint holds;
    # new rows get 0 from the model default.
    op.add_column(
        "user",
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "locked_until")
    op.drop_column("user", "failed_login_count")
