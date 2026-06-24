"""add case to attachmentownertype

Revision ID: d4e5f6a7b8c9
Revises: a1b2c3d4e5f6
Create Date: 2026-06-24 02:20:00.000000

Adds 'case' to the attachmentownertype enum so case-level attachments
can be stored (cases.py list/upload handlers use AttachmentOwnerType.case).
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE attachmentownertype ADD VALUE 'case'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    pass
