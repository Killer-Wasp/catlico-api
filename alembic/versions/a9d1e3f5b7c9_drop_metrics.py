"""drop custom metrics

Revision ID: a9d1e3f5b7c9
Revises: f6b8c0d2e4a6
Create Date: 2026-07-14 00:00:00.000000

Removes the Metric/CaseMetricValue tables. Custom metrics are redundant with
custom fields (mirroring TheHive's own deprecation of case metrics); dashboards
never consumed them (widgets read the /overview snapshot) and no UI ever
existed. No permission grants to scrub — the routes rode the generic
write:organisation / read:case / write:case permissions, which stay.

Operators who somehow stored metric data can export it BEFORE upgrading:

    SELECT m.id, m.organisation_id, m.name, m.description, m.data_type,
           m.config, m.created_at, m.created_by
    FROM metric m;

    SELECT v.case_id, v.metric_id, m.name AS metric_name, v.value, v.updated_at
    FROM case_metric_value v
    LEFT JOIN metric m ON m.id = v.metric_id;
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a9d1e3f5b7c9"
down_revision: Union[str, Sequence[str], None] = "f6b8c0d2e4a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # case_metric_value first (FK to metric). Dropping a table drops its indexes.
    op.drop_table("case_metric_value")
    op.drop_table("metric")


def downgrade() -> None:
    raise NotImplementedError(
        "Custom-metrics removal is forward-only; recreate from the "
        "h4d6f8a0c2e4 definitions if ever needed."
    )
