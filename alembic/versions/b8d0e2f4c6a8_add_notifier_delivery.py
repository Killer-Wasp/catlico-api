"""add notifier_delivery

Revision ID: b8d0e2f4c6a8
Revises: a7c9e1f3b5d7
Create Date: 2026-06-28 02:00:00.000000

Delivery audit trail for the Operational Spine notifier dispatch (A3).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'b8d0e2f4c6a8'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1f3b5d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifier_delivery',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('outbox_id', sa.Integer(), nullable=False),
        sa.Column('notifier_id', sa.Uuid(), nullable=False),
        sa.Column(
            'status', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='queued'
        ),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['outbox_id'], ['audit_outbox.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['notifier_id'], ['notifier.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'outbox_id', 'notifier_id', name='uq_notifier_delivery_outbox_notifier'
        ),
    )
    op.create_index(
        op.f('ix_notifier_delivery_outbox_id'),
        'notifier_delivery',
        ['outbox_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_notifier_delivery_notifier_id'),
        'notifier_delivery',
        ['notifier_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_notifier_delivery_status'),
        'notifier_delivery',
        ['status'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_notifier_delivery_status'), table_name='notifier_delivery')
    op.drop_index(op.f('ix_notifier_delivery_notifier_id'), table_name='notifier_delivery')
    op.drop_index(op.f('ix_notifier_delivery_outbox_id'), table_name='notifier_delivery')
    op.drop_table('notifier_delivery')
