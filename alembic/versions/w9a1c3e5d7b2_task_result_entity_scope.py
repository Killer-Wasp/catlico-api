"""task plugin-result entity ids become case-scoped composites

Revision ID: w9a1c3e5d7b2
Revises: v8f1b3d5e7a9
Create Date: 2026-07-11 12:00:00.000000

Task ids are per-case sequences (``Case.next_task_seq``), so a ``plugin_result``
row storing the bare task id as ``entity_id`` is ambiguous: task 3 of case A and
task 3 of case B — possibly in different organisations — share the same key, and
the read surface would leak results across them. Task rows now store
``"{case_id}:{task_id}"``.

Existing task rows are rewritten by joining their run's event context
(``plugin_run.event_object_id`` is the case id — task results are only accepted
under a case event). Rows whose run has been pruned (``plugin_run_id IS NULL``)
cannot be attributed to a case anymore and are deleted: they were already
unrenderable-in-principle (ambiguous key) and keeping them would preserve the
leak for whichever case happens to match the bare id.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w9a1c3e5d7b2"
down_revision: Union[str, Sequence[str], None] = "v8f1b3d5e7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Prefix the owning case id onto task rows that still have their run.
    bind.execute(
        sa.text(
            """
            UPDATE plugin_result pr
            SET entity_id = run.event_object_id || ':' || pr.entity_id
            FROM plugin_run run
            WHERE pr.plugin_run_id = run.id
              AND pr.entity_type = 'task'
              AND run.event_object_type = 'case'
              AND pr.entity_id NOT LIKE '%:%'
            """
        )
    )
    # Rows with no surviving run (or a non-case event context) cannot be scoped
    # to a case — drop them rather than leave an ambiguous, leak-prone key.
    bind.execute(
        sa.text(
            """
            DELETE FROM plugin_result
            WHERE entity_type = 'task' AND entity_id NOT LIKE '%:%'
            """
        )
    )


def downgrade() -> None:
    # Strip the case prefix back off. Reintroduces the pre-fix ambiguity, which
    # is exactly what the old code expected.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE plugin_result
            SET entity_id = split_part(entity_id, ':', 2)
            WHERE entity_type = 'task' AND entity_id LIKE '%:%'
            """
        )
    )
