"""add pattern and procedure

Revision ID: f2b4d6e8a0c2
Revises: e1a3c5d7f9b1
Create Date: 2026-06-28 06:00:00.000000

F1: MITRE ATT&CK technique catalog and case linkage.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'f2b4d6e8a0c2'
down_revision: Union[str, Sequence[str], None] = 'e1a3c5d7f9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pattern',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('tactic', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('parent_external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id', name='uq_pattern_external_id'),
    )
    op.create_index(op.f('ix_pattern_external_id'), 'pattern', ['external_id'], unique=True)
    op.create_index(op.f('ix_pattern_name'), 'pattern', ['name'], unique=False)

    op.create_table(
        'procedure',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('pattern_id', sa.Uuid(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['case_.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pattern_id'], ['pattern.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_procedure_case_id'), 'procedure', ['case_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_procedure_case_id'), table_name='procedure')
    op.drop_table('procedure')
    op.drop_index(op.f('ix_pattern_name'), table_name='pattern')
    op.drop_index(op.f('ix_pattern_external_id'), table_name='pattern')
    op.drop_table('pattern')
