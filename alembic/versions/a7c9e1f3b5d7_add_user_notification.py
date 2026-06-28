"""add user_notification

Revision ID: a7c9e1f3b5d7
Revises: e6b8d1f3a5c7
Create Date: 2026-06-28 01:00:00.000000

Per-user notification feed table for the Operational Spine (A2).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'a7c9e1f3b5d7'
down_revision: Union[str, Sequence[str], None] = 'e6b8d1f3a5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_notification',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column(
            'event_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'body', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''
        ),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['organisation_id'], ['organisation.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_user_notification_organisation_id'),
        'user_notification',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_user_notification_user_id'),
        'user_notification',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_user_notification_event_type'),
        'user_notification',
        ['event_type'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_user_notification_event_type'), table_name='user_notification'
    )
    op.drop_index(
        op.f('ix_user_notification_user_id'), table_name='user_notification'
    )
    op.drop_index(
        op.f('ix_user_notification_organisation_id'), table_name='user_notification'
    )
    op.drop_table('user_notification')
