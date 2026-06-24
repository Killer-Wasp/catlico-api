"""add org timezone and default_tlp

Revision ID: d4c7f782d2c7
Revises: e6b8d1f3a5c7
Create Date: 2026-06-24 14:33:45.812683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'd4c7f782d2c7'
down_revision: Union[str, Sequence[str], None] = 'e6b8d1f3a5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organisation', sa.Column('timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='UTC'))
    op.add_column('organisation', sa.Column('default_tlp', sa.Integer(), nullable=False, server_default='2'))


def downgrade() -> None:
    op.drop_column('organisation', 'default_tlp')
    op.drop_column('organisation', 'timezone')
