"""add must_change_password force-reset flag to user

Revision ID: b8e2d4f6a1c3
Revises: f7a9c1e3b5d2
Create Date: 2026-07-15 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e2d4f6a1c3"
# down_revision fixed at merge
down_revision: Union[str, Sequence[str], None] = "f7a9c1e3b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills existing rows so the NOT NULL constraint holds; new
    # rows get False from the model default. The flag is a password-path force-reset
    # lever set by admins — never populated at create time.
    op.add_column(
        "user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "must_change_password")
