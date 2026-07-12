"""user notification preferences

Revision ID: x1b3d5f7a9c2
Revises: w9a2c4e6f8b0
Create Date: 2026-07-12 04:00:00.000000

Per-user, per-org in-app notification mute settings (A2). A row exists only for
event types the user has explicitly configured; absence means enabled.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "x1b3d5f7a9c2"
down_revision: Union[str, Sequence[str], None] = "a5b7d9f1c3e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_notification_preference",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "organisation_id", "event_type", name="uq_user_notif_pref"
        ),
    )
    op.create_index(
        op.f("ix_user_notification_preference_user_id"),
        "user_notification_preference",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_notification_preference_organisation_id"),
        "user_notification_preference",
        ["organisation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_notification_preference_event_type"),
        "user_notification_preference",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_notification_preference_event_type"),
        table_name="user_notification_preference",
    )
    op.drop_index(
        op.f("ix_user_notification_preference_organisation_id"),
        table_name="user_notification_preference",
    )
    op.drop_index(
        op.f("ix_user_notification_preference_user_id"),
        table_name="user_notification_preference",
    )
    op.drop_table("user_notification_preference")
