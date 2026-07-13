"""drop legacy functions feature

Revision ID: f6b8c0d2e4a6
Revises: e5a7b9c1d3f2
Create Date: 2026-07-13 00:00:00.000000

Removes the Function/FunctionRun tables and the *:function permission grants.
The feature never executed real user code (FUNCTION_RUNNER_MODE was a stub);
the plugin system (plugin_dispatch / plugin-runner / SDK) supersedes it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8c0d2e4a6"
down_revision: Union[str, Sequence[str], None] = "e5a7b9c1d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # function_run first (FK to function). Dropping a table drops its indexes.
    op.drop_table("function_run")
    op.drop_table("function")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="functionrunstatus").drop(bind, checkfirst=True)
        sa.Enum(name="functiontrigger").drop(bind, checkfirst=True)
        sa.Enum(name="functionruntime").drop(bind, checkfirst=True)
    # Post-u7e0a2c4d6f8 only the 'run:function' group is stored; delete the
    # fine-grained strings too in case of stale/hand-edited rows.
    op.execute(
        "DELETE FROM role_permission WHERE permission IN "
        "('run:function', 'read:function', 'write:function', 'delete:function')"
    )
    # API-key scopes are a JSON array of permission groups; scrub run:function.
    op.execute(
        """
        UPDATE api_key
        SET scopes = (scopes::jsonb - 'run:function')::json
        WHERE scopes::jsonb @> '["run:function"]'::jsonb
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Functions feature removal is forward-only; recreate from "
        "e6b8d1f3a5c7 + e1a3c5d7f9b1 definitions if ever needed."
    )
