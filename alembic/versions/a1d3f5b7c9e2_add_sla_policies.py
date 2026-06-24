"""add sla policies

Revision ID: a1d3f5b7c9e2
Revises: d4e5f6a7b8c9
Create Date: 2026-06-24 12:00:00.000000

Org-scoped SLA policy table: one row per (org_id, severity) with
ack_seconds / resolve_seconds durations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'a1d3f5b7c9e2'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sla_policy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('ack_seconds', sa.Integer(), nullable=False),
        sa.Column('resolve_seconds', sa.Integer(), nullable=False),
        sa.Column('escalation_target', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organisation_id', 'severity', name='uq_sla_policy_org_sev'),
    )
    op.create_index(
        op.f('ix_sla_policy_organisation_id'),
        'sla_policy',
        ['organisation_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_sla_policy_organisation_id'), table_name='sla_policy')
    op.drop_table('sla_policy')
