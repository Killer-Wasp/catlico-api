"""per-user notification read receipts

Revision ID: e5a7b9c1d3f2
Revises: d4f6a8b0c2e1
Create Date: 2026-07-13 00:00:00.000000

Moves read state off the (org-shared) user_notification row into per-user
receipts. Targeted rows' read_at is backfilled 1:1; org-wide read_at cannot be
attributed to a user and is dropped (those notifications reset to unread).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7b9c1d3f2"
down_revision: Union[str, Sequence[str], None] = "d4f6a8b0c2e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_notification_read",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["user_notification.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id", "user_id", name="uq_user_notification_read"
        ),
    )
    op.create_index(
        op.f("ix_user_notification_read_user_id"),
        "user_notification_read",
        ["user_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO user_notification_read (id, notification_id, user_id, read_at)
        SELECT gen_random_uuid(), id, user_id, read_at
        FROM user_notification
        WHERE user_id IS NOT NULL AND read_at IS NOT NULL
        """
    )
    op.drop_column("user_notification", "read_at")


def downgrade() -> None:
    op.add_column(
        "user_notification",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE user_notification n
        SET read_at = r.read_at
        FROM user_notification_read r
        WHERE r.notification_id = n.id AND n.user_id = r.user_id
        """
    )
    op.drop_index(
        op.f("ix_user_notification_read_user_id"),
        table_name="user_notification_read",
    )
    op.drop_table("user_notification_read")
