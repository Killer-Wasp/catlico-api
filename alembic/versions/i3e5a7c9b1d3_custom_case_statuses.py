"""custom case statuses: native enum -> org-scoped lookup table

Revision ID: i3e5a7c9b1d3
Revises: h2c4e6a8b0d2
Create Date: 2026-07-16 00:00:00.000000

Phase 4 §4.3. Replaces the native ``casestatus`` PG enum on ``case_.status`` with
an org-scoped ``case_status`` lookup table + a FK reference (``case_.status_id``).

Steps:
  1. Create ``case_status`` (with a ``casestage`` enum for the semantic bucket).
  2. Seed the four built-in statuses per organisation (Open / In progress /
     Resolved / Duplicated). Labels are stable — API consumers reference them.
  3. Add ``case_.status_id`` (FK, ondelete RESTRICT), backfill from the old enum
     via each case's owner org: open→Open, resolved→Resolved, duplicated→Duplicated.
  4. Drop the old ``case_.status`` column and the ``casestatus`` enum type.

**Irreversible downgrade.** The old enum column is dropped and its type destroyed;
reconstructing the per-case enum from the lookup FK is not attempted. ``downgrade``
raises — restore from a backup for non-throwaway environments (matches the
precedent set by the RBAC-flatten migration).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i3e5a7c9b1d3"
down_revision: Union[str, Sequence[str], None] = "h2c4e6a8b0d2"  # down_revision fixed at merge
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    casestage = sa.Enum(
        "open", "in_progress", "closed", "duplicated", name="casestage"
    )

    op.create_table(
        "case_status",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("organisation_id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("stage", casestage, nullable=False),
        sa.Column("color", sa.String(), nullable=False, server_default="#6b7280"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_case_status_organisation_id", "case_status", ["organisation_id"]
    )
    op.create_index(
        "uq_case_status_org_label",
        "case_status",
        ["organisation_id", "label"],
        unique=True,
    )

    # --- Seed the four built-ins per org ------------------------------------
    op.execute(
        """
        INSERT INTO case_status
            (organisation_id, label, stage, color, is_builtin, hidden, position,
             created_at, created_by)
        SELECT o.id, v.label, CAST(v.stage AS casestage), v.color,
               true, false, v.position, now(), 'system'
        FROM organisation o
        CROSS JOIN (VALUES
            ('Open',        'open',        '#3b82f6', 0),
            ('In progress', 'in_progress', '#f59e0b', 1),
            ('Resolved',    'closed',      '#10b981', 2),
            ('Duplicated',  'duplicated',  '#6b7280', 3)
        ) AS v(label, stage, color, position)
        ON CONFLICT DO NOTHING
        """
    )

    # --- Add + backfill the FK reference ------------------------------------
    op.add_column("case_", sa.Column("status_id", sa.Integer(), nullable=True))

    # Map each case to its owner org's matching built-in status.
    backfill = """
        UPDATE case_ c
        SET status_id = cs.id
        FROM case_share sh
        JOIN case_status cs ON cs.organisation_id = sh.organisation_id
        WHERE c.status_id IS NULL
          AND sh.case_id = c.id
          {owner_clause}
          AND cs.is_builtin = true
          AND cs.label = (
              CASE c.status::text
                  WHEN 'open'       THEN 'Open'
                  WHEN 'resolved'   THEN 'Resolved'
                  WHEN 'duplicated' THEN 'Duplicated'
              END
          )
    """
    # Prefer the owner org; then fall back to any share for defensiveness.
    op.execute(backfill.format(owner_clause="AND sh.is_owner = true"))
    op.execute(backfill.format(owner_clause=""))

    op.create_foreign_key(
        "fk_case_status_id",
        "case_",
        "case_status",
        ["status_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("case_", "status_id", nullable=False)

    # --- Drop the old native enum column + type -----------------------------
    op.drop_column("case_", "status")
    op.execute("DROP TYPE casestatus")


def downgrade() -> None:
    raise RuntimeError(
        "Irreversible migration: the native casestatus enum was dropped and cannot "
        "be reconstructed from the case_status lookup. Restore from a backup."
    )
