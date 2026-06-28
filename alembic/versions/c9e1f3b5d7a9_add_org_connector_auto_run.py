"""add org_connector auto_run_enabled

Revision ID: c9e1f3b5d7a9
Revises: b8d0e2f4c6a8
Create Date: 2026-06-28 03:00:00.000000

Add auto_run_enabled column to org_connector for Enrichment Automation (B1).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9e1f3b5d7a9'
down_revision: Union[str, Sequence[str], None] = 'b8d0e2f4c6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'org_connector',
        sa.Column(
            'auto_run_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('org_connector', 'auto_run_enabled')
