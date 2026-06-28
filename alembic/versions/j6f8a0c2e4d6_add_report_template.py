"""add report_template

Revision ID: j6f8a0c2e4d6
Revises: i5e7f9b1d3f5
Create Date: 2026-06-28 10:00:00.000000

G4: Case report templates with markdown content and placeholders.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'j6f8a0c2e4d6'
down_revision: Union[str, Sequence[str], None] = 'i5e7f9b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_template',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('content_md', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_report_template_organisation_id'), 'report_template', ['organisation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_report_template_organisation_id'), table_name='report_template')
    op.drop_table('report_template')
