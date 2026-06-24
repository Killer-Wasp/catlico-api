"""add api keys

Revision ID: b2e4f6a8c1d3
Revises: a1d3f5b7c9e2
Create Date: 2026-06-24 12:01:00.000000

Org-scoped API keys for programmatic access. Only the sha256 `key_hash` is
persisted — the plaintext token is shown once at creation and never stored.
Soft-delete revokes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'b2e4f6a8c1d3'
down_revision: Union[str, Sequence[str], None] = 'a1d3f5b7c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_key',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organisation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prefix', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('last_four', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('key_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
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
        op.f('ix_api_key_organisation_id'), 'api_key', ['organisation_id'], unique=False
    )
    op.create_index(op.f('ix_api_key_key_hash'), 'api_key', ['key_hash'], unique=False)
    op.create_index(op.f('ix_api_key_deleted_at'), 'api_key', ['deleted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_api_key_deleted_at'), table_name='api_key')
    op.drop_index(op.f('ix_api_key_key_hash'), table_name='api_key')
    op.drop_index(op.f('ix_api_key_organisation_id'), table_name='api_key')
    op.drop_table('api_key')
