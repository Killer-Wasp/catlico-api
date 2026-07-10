"""drop legacy connector/enrichment tables

Revision ID: t6d9f1b3c5e7
Revises: s5c8e0a2b4d6
Create Date: 2026-07-10 00:00:00.000000

Retires the legacy catlico-konnect connector coupling. The connector/analyzer/
responder subsystem and its enrichment-job queue are gone; the plugin runner is
the replacement path. Per the retirement decision, historical enrichment data is
DROPPED, not migrated.

Tables removed (child-first so foreign keys resolve before their targets):
  observable_provenance -> FK into enrichment_job (source_job_id). The table only
                           ever recorded which connector job produced an imported
                           artifact; with connectors gone it has no writer and no
                           reader, so the whole table is dropped rather than left
                           as an orphan with a dangling FK + connector_name column.
  report_tag            -> FK into enrichment_job (job_id)
  enrichment_job        -> FK into connector (connector_name)
  connector_secret      -> FK into connector
  org_connector         -> FK into connector
  connector             -> dropped last, once nothing references it
"""
from typing import Sequence, Union

from alembic import op

revision: str = "t6d9f1b3c5e7"
down_revision: Union[str, Sequence[str], None] = "s5c8e0a2b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Order matters: drop every table that holds a FK into a target before the
    # target itself. Postgres DROP TABLE cascades to the table's own indexes and
    # to FK constraints defined ON it, but refuses while another live table still
    # references it — hence the child-first sequence below.
    op.drop_table("observable_provenance")  # FK -> enrichment_job
    op.drop_table("report_tag")             # FK -> enrichment_job
    op.drop_table("enrichment_job")         # FK -> connector
    op.drop_table("connector_secret")       # FK -> connector
    op.drop_table("org_connector")          # FK -> connector
    op.drop_table("connector")


def downgrade() -> None:
    # Intentionally irreversible. The connector/enrichment subsystem was retired
    # and its historical rows were dropped rather than migrated (explicit product
    # decision). There is no application model layer left for these tables, so a
    # faithful re-creation would only produce orphaned, unusable schema. Recover
    # from a pre-migration database backup if this must be undone.
    raise NotImplementedError(
        "Irreversible: legacy connector/enrichment tables were dropped as part of "
        "retiring catlico-konnect; their history was intentionally not migrated."
    )
