"""add observable_verdict and observable_provenance

Revision ID: d0f2a4c6b8e0
Revises: c9e1f3b5d7a9
Create Date: 2026-06-28 04:00:00.000000

B3: Observable verdict rollup column.
B4: Artifact provenance table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'd0f2a4c6b8e0'
down_revision: Union[str, Sequence[str], None] = 'c9e1f3b5d7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # B3: add verdict column to observable
    op.add_column(
        'observable',
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f('ix_observable_verdict'), 'observable', ['verdict'], unique=False
    )

    # B4: observable_provenance table
    op.create_table(
        'observable_provenance',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('observable_id', sa.Uuid(), nullable=False),
        sa.Column('source_job_id', sa.Uuid(), nullable=True),
        sa.Column(
            'connector_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column(
            'message', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['observable_id'], ['observable.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_job_id'], ['enrichment_job.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_observable_provenance_observable_id'),
        'observable_provenance',
        ['observable_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_observable_provenance_source_job_id'),
        'observable_provenance',
        ['source_job_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_observable_provenance_connector_name'),
        'observable_provenance',
        ['connector_name'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_observable_provenance_connector_name'),
        table_name='observable_provenance',
    )
    op.drop_index(
        op.f('ix_observable_provenance_source_job_id'),
        table_name='observable_provenance',
    )
    op.drop_index(
        op.f('ix_observable_provenance_observable_id'),
        table_name='observable_provenance',
    )
    op.drop_table('observable_provenance')
    op.drop_index(op.f('ix_observable_verdict'), table_name='observable')
    op.drop_column('observable', 'verdict')
