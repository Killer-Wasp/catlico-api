"""add refresh token device info

Revision ID: b3c5d7e9f1a2
Revises: a9d1e3f5b7c9
Create Date: 2026-07-14 09:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c5d7e9f1a2"
down_revision: Union[str, Sequence[str], None] = "a9d1e3f5b7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "refresh_token",
        sa.Column("user_agent", sa.String(length=400), nullable=True),
    )
    op.add_column(
        "refresh_token",
        sa.Column("ip_address", sa.String(length=45), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_token", "ip_address")
    op.drop_column("refresh_token", "user_agent")
