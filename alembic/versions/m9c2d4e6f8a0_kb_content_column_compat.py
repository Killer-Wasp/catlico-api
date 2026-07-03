"""compatibly rename knowledge base markdown column to content

Revision ID: m9c2d4e6f8a0
Revises: l8b1c3d5e7f9
Create Date: 2026-07-03 11:26:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m9c2d4e6f8a0"
down_revision: Union[str, Sequence[str], None] = "l8b1c3d5e7f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {
        column["name"] for column in inspector.get_columns("knowledge_base_page")
    }


def upgrade() -> None:
    columns = _column_names()
    if "content" not in columns and "markdown" in columns:
        op.alter_column("knowledge_base_page", "markdown", new_column_name="content")
    elif "content" in columns and "markdown" in columns:
        op.drop_column("knowledge_base_page", "markdown")


def downgrade() -> None:
    columns = _column_names()
    if "markdown" not in columns and "content" in columns:
        op.alter_column("knowledge_base_page", "content", new_column_name="markdown")
    elif "markdown" in columns and "content" in columns:
        op.drop_column("knowledge_base_page", "content")
