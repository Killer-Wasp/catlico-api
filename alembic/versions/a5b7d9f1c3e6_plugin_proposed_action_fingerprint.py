"""add plugin_proposed_action.fingerprint idempotency key

Revision ID: a5b7d9f1c3e6
Revises: w9a2c4e6f8b0
Create Date: 2026-07-12 03:00:00.000000

Mirrors PluginResult.fingerprint for proposed actions: a redelivered event or a
retried run (which reuses the PluginRun row) that re-proposes the same canonical
mutation must not create a duplicate proposal row for an analyst to wade through.

Adds a nullable ``fingerprint`` column plus a PARTIAL unique index on
``(plugin_run_id, fingerprint)`` WHERE ``fingerprint IS NOT NULL``. Nullable +
partial so pre-existing rows (created before this column) keep NULL fingerprints
without colliding with one another. Scoped to the run, exactly like
``uq_plugin_result_run_fingerprint`` — a genuinely different run may still
propose identical content.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5b7d9f1c3e6"
down_revision: Union[str, Sequence[str], None] = "w9a2c4e6f8b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plugin_proposed_action",
        sa.Column("fingerprint", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_plugin_proposed_action_run_fingerprint",
        "plugin_proposed_action",
        ["plugin_run_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_plugin_proposed_action_run_fingerprint",
        table_name="plugin_proposed_action",
    )
    op.drop_column("plugin_proposed_action", "fingerprint")
