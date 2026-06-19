"""add alerts

Revision ID: e3a5b2c9d1f0
Revises: d2f4a1b7c8e9
Create Date: 2026-06-13 13:40:00.000000

Org-owned inbound events with a per-org dedup key, status lifecycle, soft-delete,
and a case_id link set on promotion. The dedup uniqueness is a PARTIAL index
(WHERE deleted_at IS NULL) so a soft-deleted alert doesn't block re-ingesting its key.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3a5b2c9d1f0'
down_revision: Union[str, Sequence[str], None] = 'd2f4a1b7c8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'alert',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source_ref', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('external_link', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('tlp', sa.Integer(), nullable=False),
        sa.Column('pap', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sync_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'status',
            sa.Enum('new', 'in_progress', 'imported', 'ignored', name='alertstatus'),
            nullable=False,
        ),
        sa.Column('follow', sa.Boolean(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=True),
        sa.Column('assignee_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['assignee_id'], ['user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['case_id'], ['case_.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_alert_type'), 'alert', ['type'], unique=False)
    op.create_index(op.f('ix_alert_source'), 'alert', ['source'], unique=False)
    op.create_index(op.f('ix_alert_organisation_id'), 'alert', ['organisation_id'], unique=False)
    op.create_index(op.f('ix_alert_deleted_at'), 'alert', ['deleted_at'], unique=False)
    # Partial unique dedup index — only among live (non-deleted) alerts.
    op.create_index(
        'uq_alert_dedup',
        'alert',
        ['type', 'source', 'source_ref', 'organisation_id'],
        unique=True,
        sqlite_where=sa.text('deleted_at IS NULL'),
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_alert_dedup', table_name='alert')
    op.drop_index(op.f('ix_alert_deleted_at'), table_name='alert')
    op.drop_index(op.f('ix_alert_organisation_id'), table_name='alert')
    op.drop_index(op.f('ix_alert_source'), table_name='alert')
    op.drop_index(op.f('ix_alert_type'), table_name='alert')
    op.drop_table('alert')
