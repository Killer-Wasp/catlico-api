"""composite case-scoped ids for tasks, logs, attachments

Re-keys tasks, worklogs and case-bound attachments from opaque UUIDs to composite,
case-scoped keys — (case_id, id) for tasks/attachments, (case_id, task_id, id) for
logs — backed by per-parent counter columns. Observable attachments move to their own
UUID table (observables span cases). Pre-launch clean break: the affected tables are
dropped and recreated; no data is migrated.

Revision ID: f1a2b3c4d5e6
Revises: d4c7f782d2c7
Create Date: 2026-06-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "d4c7f782d2c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The taskstatus enum type already exists (created by the original cases/tasks
# migration and untouched here — `task` is dropped and recreated but the type
# persists). Reference it with create_type=False so recreating `task` does not
# re-emit CREATE TYPE. Use the dialect ENUM for SQLite compatibility (renders as
# a VARCHAR + CHECK there; create_type is a Postgres-only no-op on SQLite).
_task_status = postgresql.ENUM(
    "waiting", "in_progress", "completed", "cancelled",
    name="taskstatus", create_type=False,
).with_variant(
    sa.Enum(
        "waiting", "in_progress", "completed", "cancelled", name="taskstatus"
    ),
    "sqlite",
)


def upgrade() -> None:
    # Drop the UUID-keyed tables (FK-dependency order). attachment_link is
    # polymorphic (no real FK to task/log), so it can go first.
    op.drop_table("attachment_link")
    op.drop_table("task_share")
    op.drop_table("log")
    op.drop_table("task")

    # Per-case counters for the composite-keyed children.
    op.add_column(
        "case_",
        sa.Column("next_task_seq", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "case_",
        sa.Column(
            "next_attachment_seq", sa.Integer(), nullable=False, server_default="1"
        ),
    )

    op.create_table(
        "task",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("group", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", _task_status, nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_log_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["assignee_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["case_id"], ["case_.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("case_id", "id"),
    )
    op.create_index("ix_task_case_id", "task", ["case_id"])
    op.create_index("ix_task_organisation_id", "task", ["organisation_id"])
    op.create_index("ix_task_deleted_at", "task", ["deleted_at"])

    op.create_table(
        "log",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id", "task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("case_id", "task_id", "id"),
    )
    op.create_index("ix_log_case_id", "log", ["case_id"])
    op.create_index("ix_log_task_id", "log", ["task_id"])
    op.create_index("ix_log_organisation_id", "log", ["organisation_id"])
    op.create_index("ix_log_deleted_at", "log", ["deleted_at"])

    op.create_table(
        "task_share",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("case_id", "task_id", "organisation_id"),
    )

    op.create_table(
        "attachment_link",
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("owner_task_id", sa.Integer(), nullable=True),
        sa.Column("owner_log_id", sa.Integer(), nullable=True),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"], ["case_.id"], ondelete="CASCADE",
            deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "owner_task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE", deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "owner_task_id", "owner_log_id"],
            ["log.case_id", "log.task_id", "log.id"],
            ondelete="CASCADE", deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachment.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "owner_task_id IS NOT NULL OR owner_log_id IS NULL",
            name="ck_attachment_link_owner",
        ),
        sa.PrimaryKeyConstraint("case_id", "id"),
    )
    op.create_index("ix_attachment_link_case_id", "attachment_link", ["case_id"])
    op.create_index(
        "ix_attachment_link_organisation_id", "attachment_link", ["organisation_id"]
    )
    op.create_index("ix_attachment_link_deleted_at", "attachment_link", ["deleted_at"])

    op.create_table(
        "observable_attachment_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("observable_id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachment.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["observable_id"], ["observable.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observable_attachment_link_observable_id",
        "observable_attachment_link", ["observable_id"],
    )
    op.create_index(
        "ix_observable_attachment_link_organisation_id",
        "observable_attachment_link", ["organisation_id"],
    )
    op.create_index(
        "ix_observable_attachment_link_deleted_at",
        "observable_attachment_link", ["deleted_at"],
    )

    # Drop the server_default now that the columns exist — the app supplies values.
    op.alter_column("case_", "next_task_seq", server_default=None)
    op.alter_column("case_", "next_attachment_seq", server_default=None)


def downgrade() -> None:
    raise NotImplementedError(
        "Pre-launch clean break: composite-id schema is not reversible to UUID keys."
    )
