"""add notifications

Revision ID: c3f5a7b9d2e4
Revises: b2e4f6a8c1d3
Create Date: 2026-06-24 12:02:00.000000

Notifier (delivery channel: slack/email/webhook/kafka) and NotificationRule
(event → notifier bindings). Secrets are encrypted at rest and never returned.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'c3f5a7b9d2e4'
down_revision: Union[str, Sequence[str], None] = 'b2e4f6a8c1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifier',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'type',
            sa.Enum('slack', 'email', 'webhook', 'kafka', name='notifiertype'),
            nullable=False,
        ),
        sa.Column('target', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('secrets_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_notifier_organisation_id'), 'notifier', ['organisation_id'], unique=False
    )

    op.create_table(
        'notification_rule',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('event', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('notifier_ids', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_notification_rule_organisation_id'),
        'notification_rule',
        ['organisation_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_notification_rule_organisation_id'), table_name='notification_rule')
    op.drop_table('notification_rule')
    op.drop_index(op.f('ix_notifier_organisation_id'), table_name='notifier')
    op.drop_table('notifier')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        sa.Enum(name='notifiertype').drop(bind, checkfirst=True)
