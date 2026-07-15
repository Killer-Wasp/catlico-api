"""add url value to customfieldtype enum

Revision ID: f8b0d2e4c6a9
Revises: f7a9c1e3b5d2
Create Date: 2026-07-15 13:00:00.000000

Phase 4 §4.4: the `url` custom-field type is a validated string (http/https),
so it reuses the existing string_value column — only the native PG enum needs
the new label. `ADD VALUE` cannot run inside a transaction, so use an autocommit
block. The value is irreversible (Postgres has no DROP VALUE), so downgrade is a
no-op — matches the project's irreversible-enum precedent.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8b0d2e4c6a9"
down_revision: Union[str, Sequence[str], None] = "f7a9c1e3b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE customfieldtype ADD VALUE IF NOT EXISTS 'url'")


def downgrade() -> None:
    # Postgres cannot remove an enum value; leaving 'url' in place is harmless.
    pass
