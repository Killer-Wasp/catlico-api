"""pattern tactics list

Revision ID: w9a2c4e6f8b0
Revises: a4d6f8b0c2e5
Create Date: 2026-07-11 00:00:00.000000

ATT&CK techniques belong to multiple tactics; replace pattern.tactic (str)
with pattern.tactics (JSON list), backfilling by splitting on commas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w9a2c4e6f8b0"
down_revision: Union[str, Sequence[str], None] = "a4d6f8b0c2e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pattern",
        sa.Column("tactics", sa.JSON(), nullable=False, server_default="[]"),
    )
    # Backfill: split the old comma-separated value; empty string -> [].
    op.execute(
        """
        UPDATE pattern
        SET tactics = to_json(string_to_array(trim(tactic), ','))
        WHERE tactic <> ''
        """
    )
    op.drop_column("pattern", "tactic")


def downgrade() -> None:
    op.add_column(
        "pattern",
        sa.Column("tactic", sa.String(), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE pattern
        SET tactic = COALESCE(
            (SELECT string_agg(value, ',')
             FROM json_array_elements_text(tactics) AS value), '')
        """
    )
    op.drop_column("pattern", "tactics")
