"""add metrics, case_metric_value, and dashboard

Revision ID: h4d6f8a0c2e4
Revises: g3c5e7f9b1d3
Create Date: 2026-06-28 08:00:00.000000

G2: Metric definitions and case metric values.
G3: Dashboard storage.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'h4d6f8a0c2e4'
down_revision: Union[str, Sequence[str], None] = 'g3c5e7f9b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'metric',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('data_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='number'),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_metric_organisation_id'), 'metric', ['organisation_id'], unique=False)
    op.create_index(op.f('ix_metric_name'), 'metric', ['name'], unique=False)

    op.create_table(
        'case_metric_value',
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('metric_id', sa.Uuid(), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['case_.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('case_id', 'metric_id'),
    )

    op.create_table(
        'dashboard',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('layout', sa.JSON(), nullable=False),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_dashboard_organisation_id'), 'dashboard', ['organisation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_dashboard_organisation_id'), table_name='dashboard')
    op.drop_table('dashboard')
    op.drop_table('case_metric_value')
    op.drop_index(op.f('ix_metric_name'), table_name='metric')
    op.drop_index(op.f('ix_metric_organisation_id'), table_name='metric')
    op.drop_table('metric')
