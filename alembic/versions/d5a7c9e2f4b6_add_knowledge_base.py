"""add knowledge base

Revision ID: d5a7c9e2f4b6
Revises: c3f5a7b9d2e4
Create Date: 2026-06-24 12:03:00.000000

Org-scoped knowledge-base pages with typed blocks (paragraph/section/list) stored
as JSON. Backfills read/write:knowledge_base permissions onto builtin roles.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'd5a7c9e2f4b6'
down_revision: Union[str, Sequence[str], None] = 'c3f5a7b9d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'knowledge_base_page',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('blocks', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_knowledge_base_page_organisation_id'),
        'knowledge_base_page',
        ['organisation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_knowledge_base_page_title'), 'knowledge_base_page', ['title'], unique=False
    )
    op.create_index(
        op.f('ix_knowledge_base_page_deleted_at'),
        'knowledge_base_page',
        ['deleted_at'],
        unique=False,
    )

    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'read:knowledge_base' FROM role "
        "WHERE name IN ('org-admin', 'analyst', 'read-only')"
    )
    op.execute(
        "INSERT INTO role_permission (role_id, permission) "
        "SELECT id, 'write:knowledge_base' FROM role WHERE name IN ('org-admin', 'analyst')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permission "
        "WHERE permission IN ('read:knowledge_base', 'write:knowledge_base')"
    )
    op.drop_index(op.f('ix_knowledge_base_page_deleted_at'), table_name='knowledge_base_page')
    op.drop_index(op.f('ix_knowledge_base_page_title'), table_name='knowledge_base_page')
    op.drop_index(
        op.f('ix_knowledge_base_page_organisation_id'), table_name='knowledge_base_page'
    )
    op.drop_table('knowledge_base_page')
