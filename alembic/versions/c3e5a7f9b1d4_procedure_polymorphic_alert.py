"""procedure entity-polymorphic (case or alert)

Revision ID: c3e5a7f9b1d4
Revises: f8b0d2e4c6a9
Create Date: 2026-07-16 09:00:00.000000

Phase 4 §4.1a: make `procedure` entity-polymorphic so an alert can carry TTPs.
`case_id` becomes nullable, a nullable `alert_id` FK is added, and a check
constraint enforces exactly one owner (mirrors Comment / Tagging /
CustomFieldValue). Existing rows are all case-owned, so they satisfy the
constraint unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5a7f9b1d4"
down_revision: Union[str, Sequence[str], None] = "f8b0d2e4c6a9"  # down_revision fixed at merge
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("procedure", sa.Column("alert_id", sa.Integer(), nullable=True))
    op.alter_column("procedure", "case_id", existing_type=sa.Integer(), nullable=True)
    op.create_index(
        op.f("ix_procedure_alert_id"), "procedure", ["alert_id"], unique=False
    )
    op.create_foreign_key(
        "fk_procedure_alert_id_alert",
        "procedure",
        "alert",
        ["alert_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_procedure_one_owner",
        "procedure",
        "(case_id IS NULL) <> (alert_id IS NULL)",
    )


def downgrade() -> None:
    # Alert-owned procedures have no case_id and cannot survive the column going
    # NOT NULL again — drop them so the reverse constraint holds.
    op.execute("DELETE FROM procedure WHERE case_id IS NULL")
    op.drop_constraint("ck_procedure_one_owner", "procedure", type_="check")
    op.drop_constraint("fk_procedure_alert_id_alert", "procedure", type_="foreignkey")
    op.drop_index(op.f("ix_procedure_alert_id"), table_name="procedure")
    op.alter_column("procedure", "case_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("procedure", "alert_id")
