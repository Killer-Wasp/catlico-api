"""require user first_name and last_name (non-null)

Revision ID: q3a6c8e0d2f4
Revises: p2f5b7c9d1e3
Create Date: 2026-07-08 00:00:00.000000

Enforces that every user has a name. Backfills any NULL/empty first_name or
last_name (from the email local-part, falling back to a placeholder) so the
NOT NULL alter never fails on pre-existing rows, then makes both columns
NOT NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'q3a6c8e0d2f4'
down_revision: Union[str, Sequence[str], None] = 'p2f5b7c9d1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill: NULL/empty first_name -> capitalised email local-part; NULL/empty
    # last_name -> "User". Both guaranteed non-empty so the NOT NULL holds.
    op.execute(
        """
        UPDATE "user"
        SET first_name = INITCAP(NULLIF(SPLIT_PART(email, '@', 1), ''))
        WHERE first_name IS NULL OR first_name = ''
        """
    )
    op.execute(
        """
        UPDATE "user"
        SET first_name = 'User'
        WHERE first_name IS NULL OR first_name = ''
        """
    )
    op.execute(
        """
        UPDATE "user"
        SET last_name = 'User'
        WHERE last_name IS NULL OR last_name = ''
        """
    )
    op.alter_column('user', 'first_name', existing_type=sa.String(), nullable=False)
    op.alter_column('user', 'last_name', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    op.alter_column('user', 'last_name', existing_type=sa.String(), nullable=True)
    op.alter_column('user', 'first_name', existing_type=sa.String(), nullable=True)
