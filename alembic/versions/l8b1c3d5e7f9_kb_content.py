"""store knowledge base body as content

Revision ID: l8b1c3d5e7f9
Revises: k7a0b2c4d6e8
Create Date: 2026-07-03 11:15:00.000000
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "l8b1c3d5e7f9"
down_revision: Union[str, Sequence[str], None] = "k7a0b2c4d6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


knowledge_base_page = sa.table(
    "knowledge_base_page",
    sa.column("id", sa.Integer()),
    sa.column("blocks", sa.JSON()),
    sa.column("markdown", sa.String()),
    sa.column("content", sa.String()),
)


def _column_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {
        column["name"] for column in inspector.get_columns("knowledge_base_page")
    }


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _blocks_to_content(blocks: object) -> str:
    if isinstance(blocks, str):
        try:
            blocks = json.loads(blocks)
        except json.JSONDecodeError:
            return ""
    if not isinstance(blocks, list):
        return ""

    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "paragraph":
            text = str(block.get("text") or "").strip()
            code = str(block.get("code") or "").strip()
            if text:
                parts.append(text)
            if code:
                parts.append(f"```\n{code}\n```")
        elif block_type == "section":
            section_parts: list[str] = []
            title = str(block.get("title") or "").strip()
            items = block.get("items")
            if title:
                section_parts.append(f"## {title}")
            if isinstance(items, list) and items:
                section_parts.append(_bullet_list([str(item) for item in items]))
            if section_parts:
                parts.append("\n\n".join(section_parts))
        elif block_type == "list":
            items = block.get("items")
            if isinstance(items, list) and items:
                parts.append(_bullet_list([str(item) for item in items]))
    return "\n\n".join(parts)


def _content_to_blocks(content: object) -> list[dict[str, str]]:
    if not isinstance(content, str) or not content.strip():
        return []
    return [{"type": "paragraph", "text": content, "code": None}]


def upgrade() -> None:
    columns = _column_names()
    has_content = "content" in columns
    has_blocks = "blocks" in columns
    has_markdown = "markdown" in columns
    added_content = False

    if not has_content:
        op.add_column(
            "knowledge_base_page",
            sa.Column("content", sa.String(), nullable=False, server_default=""),
        )
        has_content = True
        added_content = True

    connection = op.get_bind()
    if has_blocks:
        rows = connection.execute(
            sa.select(knowledge_base_page.c.id, knowledge_base_page.c.blocks)
        )
        for row in rows:
            connection.execute(
                knowledge_base_page.update()
                .where(knowledge_base_page.c.id == row.id)
                .values(content=_blocks_to_content(row.blocks))
            )
        op.drop_column("knowledge_base_page", "blocks")
    elif has_markdown and has_content:
        rows = connection.execute(
            sa.select(knowledge_base_page.c.id, knowledge_base_page.c.markdown)
        )
        for row in rows:
            connection.execute(
                knowledge_base_page.update()
                .where(knowledge_base_page.c.id == row.id)
                .values(content=row.markdown or "")
            )

    if has_markdown:
        op.drop_column("knowledge_base_page", "markdown")

    if added_content:
        op.alter_column(
            "knowledge_base_page",
            "content",
            server_default=None,
            existing_type=sa.String(),
            existing_nullable=False,
        )


def downgrade() -> None:
    columns = _column_names()
    has_blocks = "blocks" in columns
    has_content = "content" in columns
    has_markdown = "markdown" in columns
    added_blocks = False

    if not has_blocks:
        op.add_column(
            "knowledge_base_page",
            sa.Column("blocks", sa.JSON(), nullable=False, server_default="[]"),
        )
        added_blocks = True

    connection = op.get_bind()
    if has_content:
        rows = connection.execute(
            sa.select(knowledge_base_page.c.id, knowledge_base_page.c.content)
        )
        for row in rows:
            connection.execute(
                knowledge_base_page.update()
                .where(knowledge_base_page.c.id == row.id)
                .values(blocks=_content_to_blocks(row.content))
            )
        op.drop_column("knowledge_base_page", "content")
    elif has_markdown:
        rows = connection.execute(
            sa.select(knowledge_base_page.c.id, knowledge_base_page.c.markdown)
        )
        for row in rows:
            connection.execute(
                knowledge_base_page.update()
                .where(knowledge_base_page.c.id == row.id)
                .values(blocks=_content_to_blocks(row.markdown))
            )
        op.drop_column("knowledge_base_page", "markdown")

    if added_blocks:
        op.alter_column(
            "knowledge_base_page",
            "blocks",
            server_default=None,
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
