"""kb version history

Revision ID: n0d3f5a7b9c1
Revises: m9c2d4e6f8a0
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n0d3f5a7b9c1"
down_revision: str | None = "m9c2d4e6f8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base_page_version",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("changed_fields", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("edited_by", sa.String(), nullable=False),
        sa.Column("edited_by_email", sa.String(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("reverted_from_version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["knowledge_base_page.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reverted_from_version_id"], ["knowledge_base_page_version.id"]
        ),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'revert', 'import')",
            name="ck_kb_page_version_action",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "page_id", "version_number", name="uq_kb_page_version_page_number"
        ),
    )
    op.create_index(
        "ix_knowledge_base_page_version_page_id",
        "knowledge_base_page_version",
        ["page_id"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_organisation_id",
        "knowledge_base_page_version",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_version_number",
        "knowledge_base_page_version",
        ["version_number"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_action",
        "knowledge_base_page_version",
        ["action"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_base_page_version_action",
        table_name="knowledge_base_page_version",
    )
    op.drop_index(
        "ix_knowledge_base_page_version_version_number",
        table_name="knowledge_base_page_version",
    )
    op.drop_index(
        "ix_knowledge_base_page_version_organisation_id",
        table_name="knowledge_base_page_version",
    )
    op.drop_index(
        "ix_knowledge_base_page_version_page_id",
        table_name="knowledge_base_page_version",
    )
    op.drop_table("knowledge_base_page_version")
