"""drop plugin_runner enrollment columns

Revision ID: t6d9f1a3c5e7
Revises: f7a9c1e3b5d2
Create Date: 2026-07-15 00:00:00.000000

Collapse the three-secret enrollment dance to a single shared secret. The
runner now authenticates internal calls with ``Authorization: Bearer
<shared_secret>`` + ``X-Runner-Id`` and self-registers, so the per-runner
enrollment/credential/push-secret state on ``plugin_runner`` is obsolete.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "t6d9f1a3c5e7"
# down_revision fixed at merge
down_revision: Union[str, Sequence[str], None] = "b8e2d4f6a1c3"  # chained behind wave1-a at integration
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("plugin_runner", "enrollment_state")
    op.drop_column("plugin_runner", "credential_hash")
    op.drop_column("plugin_runner", "enrollment_token_hash")
    op.drop_column("plugin_runner", "enrollment_token_expires_at")
    op.drop_column("plugin_runner", "push_signing_secret_encrypted")


def downgrade() -> None:
    op.add_column(
        "plugin_runner",
        sa.Column(
            "push_signing_secret_encrypted",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "plugin_runner",
        sa.Column(
            "enrollment_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "plugin_runner",
        sa.Column(
            "enrollment_token_hash",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "plugin_runner",
        sa.Column(
            "credential_hash",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "plugin_runner",
        sa.Column(
            "enrollment_state",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="pending",
        ),
    )
