"""add password_reset_token

Revision ID: i5e7f9b1d3f5
Revises: h4d6f8a0c2e4
Create Date: 2026-06-28 09:00:00.000000

G6: Password reset token for forgot/reset flow.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'i5e7f9b1d3f5'
down_revision: Union[str, Sequence[str], None] = 'h4d6f8a0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_reset_token',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_password_reset_token_user_id'), 'password_reset_token', ['user_id'], unique=False)
    op.create_index(op.f('ix_password_reset_token_token_hash'), 'password_reset_token', ['token_hash'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_password_reset_token_token_hash'), table_name='password_reset_token')
    op.drop_index(op.f('ix_password_reset_token_user_id'), table_name='password_reset_token')
    op.drop_table('password_reset_token')
