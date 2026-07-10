"""add search structures

Revision ID: s5c8e0a2b4d6
Revises: r4b7d9e1f3a5
Create Date: 2026-07-10 00:00:00.000000

Global search: generated tsvector columns + GIN indexes on case_/alert/task/
comment/observable, pg_trgm on observable.data for substring matching, and an
app-maintained observable.ip inet column (GiST) for CIDR containment.
See docs/global-search-design.md (repo root).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "s5c8e0a2b4d6"
down_revision: Union[str, Sequence[str], None] = "r4b7d9e1f3a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, generated-column expression). 'simple' config: no stemming — literal
# tokens matter more than stemming for domains/filenames/refs (see spec).
_TSV: list[tuple[str, str]] = [
    (
        "case_",
        "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('simple', coalesce(description, '')), 'B') || "
        "setweight(to_tsvector('simple', coalesce(summary, '')), 'B')",
    ),
    (
        "alert",
        "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('simple', coalesce(description, '')), 'B') || "
        "setweight(to_tsvector('simple', coalesce(source_ref, '')), 'B')",
    ),
    (
        "task",
        "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('simple', coalesce(\"group\", '')), 'B') || "
        "setweight(to_tsvector('simple', coalesce(description, '')), 'B')",
    ),
    ("comment", "to_tsvector('simple', coalesce(message, ''))"),
    ("observable", "to_tsvector('simple', coalesce(message, ''))"),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for table, expr in _TSV:
        op.execute(
            f'ALTER TABLE "{table}" ADD COLUMN search_tsv tsvector '
            f"GENERATED ALWAYS AS ({expr}) STORED"
        )
        op.execute(
            f'CREATE INDEX ix_{table}_search_tsv ON "{table}" USING gin (search_tsv)'
        )

    op.execute(
        "CREATE INDEX ix_observable_data_trgm ON observable "
        "USING gin (data gin_trgm_ops)"
    )

    op.execute("ALTER TABLE observable ADD COLUMN ip inet")
    op.execute("CREATE INDEX ix_observable_ip ON observable USING gist (ip inet_ops)")
    # Backfill: parse-guarded cast; unparseable data stays NULL.
    op.execute(
        """
        CREATE FUNCTION pg_temp.try_inet(t text) RETURNS inet AS $$
        BEGIN
            RETURN t::inet;
        EXCEPTION WHEN OTHERS THEN
            RETURN NULL;
        END $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "UPDATE observable SET ip = pg_temp.try_inet(data) "
        "WHERE observable_type = 'ip'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_observable_ip")
    op.execute("ALTER TABLE observable DROP COLUMN ip")
    op.execute("DROP INDEX ix_observable_data_trgm")
    for table, _ in _TSV:
        op.execute(f"DROP INDEX ix_{table}_search_tsv")
        op.execute(f'ALTER TABLE "{table}" DROP COLUMN search_tsv')
