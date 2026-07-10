"""add plugin_runner

Revision ID: r4b7d9e1f3a5
Revises: q3a6c8e0d2f4
Create Date: 2026-07-08 00:00:00.000000

Plugin runner engine: a registered runner instance that hosts plugins.
Phase 1: introduce alongside existing connector models.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "r4b7d9e1f3a5"
down_revision: Union[str, Sequence[str], None] = "q3a6c8e0d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plugin_runner",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("base_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("isolation_mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enrollment_state", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("credential_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("enrollment_token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("enrollment_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("push_signing_secret_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "plugin_version",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("commit_sha", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("image_digest", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("install_log", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("health_status", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "plugin_definition",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("active_version_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(
            ["active_version_id"],
            ["plugin_version.id"],
            name="fk_plugin_definition_active_version_id_plugin_version",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add FK from plugin_version to plugin_definition (circular dep resolved by
    # creating tables separately)
    op.create_foreign_key(
        "fk_plugin_version_plugin_id_plugin_definition",
        "plugin_version",
        "plugin_definition",
        ["plugin_id"],
        ["id"],
    )

    op.create_table(
        "runner_plugin_installation",
        sa.Column("runner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_version_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("install_status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("health_status", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["runner_id"], ["plugin_runner.id"]),
        sa.ForeignKeyConstraint(["plugin_version_id"], ["plugin_version.id"]),
        sa.PrimaryKeyConstraint("runner_id", "plugin_version_id"),
    )

    op.create_table(
        "org_plugin",
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auto_run_enabled", sa.Boolean(), nullable=False),
        sa.Column("trigger_overrides", sa.JSON(), nullable=True),
        sa.Column("schedule_override", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("suspended_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("auto_apply_actions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organisation_id", "plugin_id"),
    )

    op.create_table(
        "plugin_config",
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("secrets_encrypted", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organisation_id", "plugin_id"),
    )

    op.create_table(
        "plugin_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_version_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("runner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_object_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("event_object_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("runtime_token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("runtime_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("skip_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_message", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error_kind", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("log_tail", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("operation_count", sa.Integer(), nullable=False),
        sa.Column("rolled_up", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"]),
        sa.ForeignKeyConstraint(["runner_id"], ["plugin_runner.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "plugin_id", name="uq_plugin_run_event_plugin"),
    )
    op.create_index(
        op.f("ix_plugin_run_runtime_token_hash"),
        "plugin_run",
        ["runtime_token_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plugin_run_rolled_up"),
        "plugin_run",
        ["rolled_up"],
        unique=False,
    )

    op.create_table(
        "plugin_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        # Nullable + SET NULL: results outlive their run (pruned at 30d) and are
        # retained on their own schedule; run pruning must not cascade-delete them.
        sa.Column("plugin_run_id", sa.Uuid(), nullable=True),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_version_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("verdict", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("render_mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("normalized_data", sa.JSON(), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("result_metadata", sa.JSON(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plugin_run_id"], ["plugin_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_run_id",
            "fingerprint",
            name="uq_plugin_result_run_fingerprint",
        ),
    )

    op.create_table(
        "plugin_run_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        # SET NULL like plugin_result: an attachment outlives its run so results
        # that reference it keep working after the run is pruned.
        sa.Column("plugin_run_id", sa.Uuid(), nullable=True),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("purpose", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plugin_run_id"], ["plugin_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"]),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "plugin_proposed_action",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plugin_run_id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("action_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("decision_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("decided_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["plugin_run_id"], ["plugin_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "plugin_run_daily",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("plugin_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("timeout_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("total_duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugin_definition.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organisation_id",
            "plugin_id",
            "day",
            name="uq_plugin_run_daily_org_plugin_day",
        ),
    )

    op.create_table(
        "plugin_event_delivery",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("runner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("envelope", sa.JSON(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["runner_id"], ["plugin_runner.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "runner_id", name="uq_plugin_delivery_event_runner"),
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_plugin_run_rolled_up"), table_name="plugin_run")
    op.drop_index(op.f("ix_plugin_run_runtime_token_hash"), table_name="plugin_run")
    op.drop_table("plugin_event_delivery")
    op.drop_table("plugin_run_daily")
    op.drop_table("plugin_proposed_action")
    op.drop_table("plugin_run_file")
    op.drop_table("plugin_result")
    op.drop_table("plugin_run")
    op.drop_table("plugin_config")
    op.drop_table("org_plugin")
    op.drop_table("runner_plugin_installation")
    op.drop_constraint(
        "fk_plugin_version_plugin_id_plugin_definition",
        "plugin_version",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_plugin_definition_active_version_id_plugin_version",
        "plugin_definition",
        type_="foreignkey",
    )
    op.drop_table("plugin_definition")
    op.drop_table("plugin_version")
    op.drop_table("plugin_runner")
