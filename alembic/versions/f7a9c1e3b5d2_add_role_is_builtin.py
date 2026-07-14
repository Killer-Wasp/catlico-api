"""add is_builtin flag to role

Revision ID: f7a9c1e3b5d2
Revises: e5c9a1b3d7f0
Create Date: 2026-07-15 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a9c1e3b5d2"
down_revision: Union[str, Sequence[str], None] = "e5c9a1b3d7f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The built-in role names are a stable, protected vocabulary seeded per-org. Every
# org gets exactly these three, and role names are unique per org, so a custom role
# can never share one of these names — making name-membership a safe backfill proxy.
_BUILTIN_ROLE_NAMES = ("org-admin", "analyst", "read-only")


def upgrade() -> None:
    # server_default backfills existing rows so the NOT NULL constraint holds; new
    # rows get False from the model default.
    op.add_column(
        "role",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Stamp already-seeded built-in rows so orgs created before this feature end up
    # correctly flagged even if the startup re-seed upsert never runs.
    op.execute(
        sa.text("UPDATE role SET is_builtin = true WHERE name IN :names").bindparams(
            sa.bindparam("names", _BUILTIN_ROLE_NAMES, expanding=True)
        )
    )


def downgrade() -> None:
    op.drop_column("role", "is_builtin")
