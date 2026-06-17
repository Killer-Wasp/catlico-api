"""case soft delete

Revision ID: 0a1b2c3d4e5f
Revises: c5e7d9a1f2b8
Create Date: 2026-06-14 00:00:00.000000

Adds deleted_at/deleted_by to case_ so cases soft-delete like their children
(tasks, logs, observables, comments) instead of being physically removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = 'c5e7d9a1f2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('case_', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column(
        'case_',
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(op.f('ix_case__deleted_at'), 'case_', ['deleted_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_case__deleted_at'), table_name='case_')
    op.drop_column('case_', 'deleted_by')
    op.drop_column('case_', 'deleted_at')
