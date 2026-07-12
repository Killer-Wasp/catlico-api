"""kb_page_version datetime columns become timestamptz

Revision ID: x1a3c5e7d9b0
Revises: w9a1c3e5d7b2
Create Date: 2026-07-12 00:00:00.000000

``knowledge_base_page_version`` was created (migration ``n0d3f5a7b9c1``) with
``created_at`` / ``updated_at`` / ``edited_at`` as ``timestamp without time zone``
— the only naive datetime columns left in the schema. Every other table uses
``timestamptz`` and the app is now tz-aware UTC end-to-end, so these three are
promoted to match.

The stored naive values are already UTC wall-clock instants, so the conversion is
lossless: ``USING col AT TIME ZONE 'UTC'`` reinterprets each naive value as UTC and
yields the identical instant. Downgrade reverses it (``AT TIME ZONE 'UTC'`` on a
timestamptz projects back to the UTC naive wall-clock).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "x1a3c5e7d9b0"
down_revision: Union[str, Sequence[str], None] = "w9a1c3e5d7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "knowledge_base_page_version"
_COLUMNS = ("created_at", "updated_at", "edited_at")


def upgrade() -> None:
    for col in _COLUMNS:
        op.execute(
            f'ALTER TABLE {_TABLE} '
            f'ALTER COLUMN {col} TYPE timestamptz '
            f"USING {col} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for col in _COLUMNS:
        op.execute(
            f'ALTER TABLE {_TABLE} '
            f'ALTER COLUMN {col} TYPE timestamp '
            f"USING {col} AT TIME ZONE 'UTC'"
        )
