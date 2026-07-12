"""add org_plugin.config_failure_streak for the config circuit breaker

Revision ID: a4d6f8b0c2e5
Revises: z3c5e7f9b1d4
Create Date: 2026-07-12 03:00:00.000000

The config circuit breaker counts consecutive ``config``-kind run failures per
``(organisation, plugin)`` and auto-suspends (sets ``suspended_reason``) at a
threshold. This adds the counter; existing rows start at 0.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4d6f8b0c2e5"
down_revision: Union[str, Sequence[str], None] = "z3c5e7f9b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "org_plugin",
        sa.Column(
            "config_failure_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("org_plugin", "config_failure_streak")
