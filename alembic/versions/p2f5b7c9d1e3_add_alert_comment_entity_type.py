"""add 'alert' to commententitytype enum

Revision ID: p2f5b7c9d1e3
Revises: o1e4a6b8c0d2
Create Date: 2026-07-07 00:00:00.000000

Comments became polymorphic across cases and alerts. The `entity_type` column is
a native Postgres enum (`commententitytype`), so the new `alert` label has to be
added to the type before alert comments can be inserted.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'p2f5b7c9d1e3'
down_revision: Union[str, Sequence[str], None] = 'o1e4a6b8c0d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE commententitytype ADD VALUE IF NOT EXISTS 'alert'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type; removing 'alert' would
    # require recreating the type and rewriting every dependent column. Left as a
    # no-op — the unused label is harmless.
    pass
