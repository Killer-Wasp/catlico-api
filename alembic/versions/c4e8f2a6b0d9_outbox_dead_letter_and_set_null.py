"""outbox dead-letter + SET NULL child FKs

Revision ID: c4e8f2a6b0d9
Revises: b3c5d7e9f1a2
Create Date: 2026-07-14 00:00:00.000000

Two changes that make outbox retention pruning safe (plans/phase-2 §2.4):

1. A ``dead_lettered_at`` marker on ``audit_outbox`` so the drain can give up on a
   row that has exhausted ``MAX_OUTBOX_ATTEMPTS`` instead of retrying it forever.
2. Flip the two child FKs that reference ``audit_outbox`` from ON DELETE CASCADE to
   ON DELETE SET NULL (and make ``notifier_delivery.outbox_id`` nullable). Under
   CASCADE, pruning old outbox rows would cascade-delete users' notification
   history and the delivery ledger; SET NULL keeps the child rows and just drops
   their provenance link.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8f2a6b0d9"
down_revision: Union[str, Sequence[str], None] = "b3c5d7e9f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_outbox",
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )

    # user_notification.outbox_id is already nullable — only the delete rule changes.
    op.drop_constraint(
        "fk_user_notification_outbox_id", "user_notification", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_user_notification_outbox_id",
        "user_notification",
        "audit_outbox",
        ["outbox_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # notifier_delivery.outbox_id must become nullable to hold the SET NULL result.
    op.alter_column("notifier_delivery", "outbox_id", existing_type=sa.Integer(), nullable=True)
    op.drop_constraint(
        "notifier_delivery_outbox_id_fkey", "notifier_delivery", type_="foreignkey"
    )
    op.create_foreign_key(
        "notifier_delivery_outbox_id_fkey",
        "notifier_delivery",
        "audit_outbox",
        ["outbox_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "notifier_delivery_outbox_id_fkey", "notifier_delivery", type_="foreignkey"
    )
    op.create_foreign_key(
        "notifier_delivery_outbox_id_fkey",
        "notifier_delivery",
        "audit_outbox",
        ["outbox_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("notifier_delivery", "outbox_id", existing_type=sa.Integer(), nullable=False)

    op.drop_constraint(
        "fk_user_notification_outbox_id", "user_notification", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_user_notification_outbox_id",
        "user_notification",
        "audit_outbox",
        ["outbox_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_column("audit_outbox", "dead_lettered_at")
