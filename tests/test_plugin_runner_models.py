from sqlalchemy import UniqueConstraint

from app.models.plugin_runner import (
    OrgPlugin,
    PluginDefinition,
    PluginProposedAction,
    PluginResult,
    PluginRun,
    PluginRunFile,
    PluginRunDaily,
    PluginRunner,
    RunnerPluginInstallation,
)


def _unique_column_sets(table) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_plugin_definition_uses_installations_for_multi_runner_support():
    assert "runner_id" not in PluginDefinition.__table__.columns

    installation_columns = RunnerPluginInstallation.__table__.columns
    assert installation_columns["runner_id"].primary_key is True
    assert installation_columns["plugin_version_id"].primary_key is True
    assert "install_status" in installation_columns
    assert "health_status" in installation_columns
    assert "last_seen_at" in installation_columns


def test_plugin_run_has_claim_and_liveness_fields():
    columns = PluginRun.__table__.columns
    assert ("event_id", "plugin_id") in _unique_column_sets(PluginRun.__table__)

    for column_name in (
        "attempt",
        "error_kind",
        "log_tail",
        "last_heartbeat_at",
        "progress_message",
        "progress_percent",
        "progress_updated_at",
    ):
        assert column_name in columns

    status_default = columns["status"].default.arg
    assert status_default == "queued"


def test_plugin_foundation_models_match_phase_one_contract():
    runner_columns = PluginRunner.__table__.columns
    # The enrollment/credential columns were dropped when runner auth collapsed to
    # a single shared secret (no per-runner state persisted).
    assert "enrollment_state" not in runner_columns
    assert "credential_hash" not in runner_columns
    assert "enrollment_token_hash" not in runner_columns
    assert "push_signing_secret_encrypted" not in runner_columns

    org_plugin_columns = OrgPlugin.__table__.columns
    assert "schedule_override" in org_plugin_columns
    assert "suspended_reason" in org_plugin_columns

    result_columns = PluginResult.__table__.columns
    for column_name in (
        "entity_type",
        "entity_id",
        "confidence",
        "render_mode",
        "attachments",
        "fingerprint",
        "expires_at",
    ):
        assert column_name in result_columns
    assert ("plugin_run_id", "fingerprint") in _unique_column_sets(PluginResult.__table__)

    run_file_columns = PluginRunFile.__table__.columns
    for column_name in (
        "plugin_run_id",
        "attachment_id",
        "filename",
        "content_type",
        "size",
        "sha256",
    ):
        assert column_name in run_file_columns

    assert PluginProposedAction.__tablename__ == "plugin_proposed_action"
    assert PluginRunDaily.__tablename__ == "plugin_run_daily"
