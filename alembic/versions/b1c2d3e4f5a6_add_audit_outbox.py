"""add audit and audit_outbox

Revision ID: b1c2d3e4f5a6
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-14 00:00:00.000000

Per-mutation audit trail + durable post-commit fan-out queue. `details`/`payload`
are generic JSON (json on Postgres, TEXT on SQLite) to match the codebase's JSON
convention; polymorphic targets are string columns, not FKs, so rows survive a
hard-delete of their target.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = '0a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('main_action', sa.Boolean(), nullable=False),
        sa.Column('object_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('object_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('context_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('context_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('actor', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_audit_request_id'), 'audit', ['request_id'], unique=False)
    op.create_index('ix_audit_object', 'audit', ['object_type', 'object_id'], unique=False)
    op.create_index('ix_audit_context', 'audit', ['context_type', 'context_id'], unique=False)

    op.create_table(
        'audit_outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('audit_id', sa.Integer(), nullable=False),
        sa.Column('topic', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['audit_id'], ['audit.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('audit_id', 'topic', name='uq_audit_outbox_audit_topic'),
    )
    op.create_index(
        op.f('ix_audit_outbox_audit_id'), 'audit_outbox', ['audit_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_audit_outbox_audit_id'), table_name='audit_outbox')
    op.drop_table('audit_outbox')
    op.drop_index('ix_audit_context', table_name='audit')
    op.drop_index('ix_audit_object', table_name='audit')
    op.drop_index(op.f('ix_audit_request_id'), table_name='audit')
    op.drop_table('audit')
