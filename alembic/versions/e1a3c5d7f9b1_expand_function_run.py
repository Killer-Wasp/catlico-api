"""expand function_run statuses and fields

Revision ID: e1a3c5d7f9b1
Revises: d0f2a4c6b8e0
Create Date: 2026-06-28 05:00:00.000000

D1: Add queued/running/timeout/cancelled statuses, input/output/context/dedup fields.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1a3c5d7f9b1'
down_revision: Union[str, Sequence[str], None] = 'd0f2a4c6b8e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column('function_run', sa.Column('input', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('function_run', sa.Column('output', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('function_run', sa.Column('context_type', sa.String(), nullable=True))
    op.add_column('function_run', sa.Column('context_id', sa.String(), nullable=True))
    op.add_column('function_run', sa.Column('dedup_key', sa.String(), nullable=True))
    op.add_column('function_run', sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True))

    # Make started_at nullable (was non-nullable before)
    op.alter_column('function_run', 'started_at', nullable=True,
                    existing_type=sa.DateTime(timezone=True))

    # Update default values for existing columns
    op.alter_column('function_run', 'duration_ms',
                    existing_type=sa.Integer(), server_default='0',
                    existing_server_default=None)
    op.alter_column('function_run', 'attempts',
                    existing_type=sa.Integer(), server_default='0',
                    existing_server_default=sa.text('1'))

    # Create index on dedup_key
    op.create_index(op.f('ix_function_run_dedup_key'), 'function_run', ['dedup_key'], unique=False)

    # Alter the enum type to add new values
    op.execute("ALTER TYPE functionrunstatus ADD VALUE IF NOT EXISTS 'queued'")
    op.execute("ALTER TYPE functionrunstatus ADD VALUE IF NOT EXISTS 'running'")
    op.execute("ALTER TYPE functionrunstatus ADD VALUE IF NOT EXISTS 'timeout'")
    op.execute("ALTER TYPE functionrunstatus ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Cannot remove enum values in PostgreSQL; skip enum changes
    op.drop_index(op.f('ix_function_run_dedup_key'), table_name='function_run')
    op.alter_column('function_run', 'started_at', nullable=False,
                    existing_type=sa.DateTime(timezone=True))
    op.drop_column('function_run', 'ended_at')
    op.drop_column('function_run', 'dedup_key')
    op.drop_column('function_run', 'context_id')
    op.drop_column('function_run', 'context_type')
    op.drop_column('function_run', 'output')
    op.drop_column('function_run', 'input')
