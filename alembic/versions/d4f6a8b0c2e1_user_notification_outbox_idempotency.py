"""user_notification outbox_id + idempotency indexes

Revision ID: d4f6a8b0c2e1
Revises: x1b3d5f7a9c2
Create Date: 2026-07-13 00:00:00.000000

Adds the outbox provenance column and two partial unique indexes forming the
retry-idempotency key: one org-wide row per outbox event, one targeted row per
(outbox event, user). Two partial indexes because Postgres unique treats NULL
user_id values as distinct.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8b0c2e1"
down_revision: Union[str, Sequence[str], None] = "x1b3d5f7a9c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_notification", sa.Column("outbox_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_user_notification_outbox_id",
        "user_notification",
        "audit_outbox",
        ["outbox_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_user_notification_outbox_id"),
        "user_notification",
        ["outbox_id"],
        unique=False,
    )
    op.create_index(
        "uq_user_notification_outbox_orgwide",
        "user_notification",
        ["outbox_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL AND outbox_id IS NOT NULL"),
    )
    op.create_index(
        "uq_user_notification_outbox_user",
        "user_notification",
        ["outbox_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL AND outbox_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_notification_outbox_user", table_name="user_notification")
    op.drop_index("uq_user_notification_outbox_orgwide", table_name="user_notification")
    op.drop_index(op.f("ix_user_notification_outbox_id"), table_name="user_notification")
    op.drop_constraint(
        "fk_user_notification_outbox_id", "user_notification", type_="foreignkey"
    )
    op.drop_column("user_notification", "outbox_id")
