"""index audit_outbox.dead_lettered_at (partial)

Revision ID: s5c8e0f2a4b6
Revises: c4e8f2a6b0d9
Create Date: 2026-07-14 00:00:00.000000

Adds a partial index on ``audit_outbox.dead_lettered_at`` covering only
dead-lettered rows (``WHERE dead_lettered_at IS NOT NULL``).

This backs the platform-wide dead-letter COUNT in ``crud/overview.py``
(``_dead_letter_count``), which runs on **every** overview/dashboard build and
otherwise falls back to a seq scan over the whole outbox. The partial predicate
keeps the index tiny — it only holds the (normally few) dead-lettered rows, which
is exactly the set the COUNT touches. The matching index lives on the
``AuditOutbox`` model's ``__table_args__`` so ``alembic check`` stays in sync.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "s5c8e0f2a4b6"
down_revision: Union[str, Sequence[str], None] = "c4e8f2a6b0d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_outbox_dead_lettered_at",
        "audit_outbox",
        ["dead_lettered_at"],
        unique=False,
        postgresql_where="dead_lettered_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_audit_outbox_dead_lettered_at", table_name="audit_outbox")
