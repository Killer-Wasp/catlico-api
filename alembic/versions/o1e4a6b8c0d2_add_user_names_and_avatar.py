"""add first_name, last_name and avatar to user

Revision ID: o1e4a6b8c0d2
Revises: n0d3f5a7b9c1
Create Date: 2026-07-03 00:00:00.000000

Adds profile fields to the user table: first_name, last_name, and a nullable
avatar_attachment_id referencing the content-addressed blob store (attachment).
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'o1e4a6b8c0d2'
down_revision: Union[str, Sequence[str], None] = 'n0d3f5a7b9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column('first_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'user',
        sa.Column('last_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'user',
        sa.Column('avatar_attachment_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_user_avatar_attachment_id_attachment',
        'user',
        'attachment',
        ['avatar_attachment_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_user_avatar_attachment_id_attachment', 'user', type_='foreignkey'
    )
    op.drop_column('user', 'avatar_attachment_id')
    op.drop_column('user', 'last_name')
    op.drop_column('user', 'first_name')
