"""merge heads f1a2b3c4d5e6 and j6f8a0c2e4d6

Revision ID: k7a0b2c4d6e8
Revises: f1a2b3c4d5e6, j6f8a0c2e4d6
Create Date: 2026-06-28 12:00:00.000000

Merge the pre-existing composite-case-scoped-ids head with the new
operational-spine/enrichment/auth/threat-intel/analytics chain.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'k7a0b2c4d6e8'
down_revision: Union[str, Sequence[str], None] = ('f1a2b3c4d5e6', 'j6f8a0c2e4d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
