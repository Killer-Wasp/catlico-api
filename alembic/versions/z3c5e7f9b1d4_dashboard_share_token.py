"""add dashboard.share_token_hash for public read-only share links

Revision ID: z3c5e7f9b1d4
Revises: y2b4d6f8a0c2
Create Date: 2026-07-12 02:00:00.000000

Adds a nullable ``share_token_hash`` (SHA-256 hex of a 256-bit random token) plus
an index for the public-view lookup. NULL means no active share link. The
plaintext token is never stored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z3c5e7f9b1d4"
down_revision: Union[str, Sequence[str], None] = "y2b4d6f8a0c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dashboard",
        sa.Column("share_token_hash", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_dashboard_share_token_hash",
        "dashboard",
        ["share_token_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dashboard_share_token_hash", table_name="dashboard")
    op.drop_column("dashboard", "share_token_hash")
