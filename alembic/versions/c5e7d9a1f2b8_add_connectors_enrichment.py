"""add connectors and enrichment

Revision ID: c5e7d9a1f2b8
Revises: b8d1f4a6c2e3
Create Date: 2026-06-14 02:10:00.000000

Connector/analyzer engine: a global connector catalog the catlico-connector-engine service
self-registers into, global super-admin-managed settings+secrets, per-org enable/
disable, an enrichment_job work queue (pull + lease), and report_tag verdict badges.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5e7d9a1f2b8'
down_revision: Union[str, Sequence[str], None] = 'b8d1f4a6c2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'connector',
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('connector_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('data_types', sa.JSON(), nullable=True),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('manifest', sa.JSON(), nullable=True),
        sa.Column('available', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('name'),
    )

    op.create_table(
        'connector_secret',
        sa.Column('connector_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('secrets_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['connector_name'], ['connector.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('connector_name'),
    )

    op.create_table(
        'org_connector',
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('connector_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['connector_name'], ['connector.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('organisation_id', 'connector_name'),
    )

    op.create_table(
        'enrichment_job',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('observable_id', sa.Uuid(), nullable=False),
        sa.Column('connector_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('connector_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('data_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('data', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('tlp', sa.Integer(), nullable=False),
        sa.Column('pap', sa.Integer(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('cache_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('report', sa.JSON(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('from_cache', sa.Boolean(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_token', sa.Uuid(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('queued_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['connector_name'], ['connector.name']),
        sa.ForeignKeyConstraint(['observable_id'], ['observable.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_enrichment_job_cache_key', 'enrichment_job', ['cache_key'], unique=False)
    op.create_index('ix_enrichment_job_status', 'enrichment_job', ['status'], unique=False)

    op.create_table(
        'report_tag',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('observable_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('connector_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('namespace', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('predicate', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('level', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['enrichment_job.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['observable_id'], ['observable.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_report_tag_observable_id', 'report_tag', ['observable_id'], unique=False
    )
    op.create_index(
        op.f('ix_report_tag_connector_name'), 'report_tag', ['connector_name'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_report_tag_connector_name'), table_name='report_tag')
    op.drop_index('ix_report_tag_observable_id', table_name='report_tag')
    op.drop_table('report_tag')
    op.drop_index('ix_enrichment_job_status', table_name='enrichment_job')
    op.drop_index('ix_enrichment_job_cache_key', table_name='enrichment_job')
    op.drop_table('enrichment_job')
    op.drop_table('org_connector')
    op.drop_table('connector_secret')
    op.drop_table('connector')
