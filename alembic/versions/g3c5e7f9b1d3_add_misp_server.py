"""add misp_server

Revision ID: g3c5e7f9b1d3
Revises: f2b4d6e8a0c2
Create Date: 2026-06-28 07:00:00.000000

F4: MISP server configuration for import/export.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'g3c5e7f9b1d3'
down_revision: Union[str, Sequence[str], None] = 'f2b4d6e8a0c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'misp_server',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('auth_key_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('verify_ssl', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_misp_server_organisation_id'), 'misp_server', ['organisation_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_misp_server_organisation_id'), table_name='misp_server')
    op.drop_table('misp_server')
