"""Guards for the tz-aware-UTC-end-to-end datetime convention.

All timestamps in this app are tz-aware UTC, stored in Postgres ``timestamptz``.
These guards fail the build if a naive timestamp is reintroduced — the failure
mode is silent (naive/aware comparisons raise ``TypeError`` only on the paths that
happen to mix them), so a static + structural check is the cheapest way to keep it
from creeping back. See app/models/AGENTS.md.
"""
import pathlib

from sqlalchemy import DateTime

import app.models  # noqa: F401  (registers every table on the shared metadata)
from app.models.common import CreatedMixin, TimestampMixin, utcnow
from app.models.knowledge_base import KnowledgeBasePageVersion
from sqlmodel import SQLModel

_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# The idioms that (re)introduce naive datetimes. `datetime.now()` / `utcnow()`
# without an explicit tz are naive; `.replace(tzinfo=None)` strips awareness.
_FORBIDDEN = (
    "tzinfo=None",  # strips awareness (bare or embedded in .replace(...))
    "datetime.utcnow(",
    "datetime.now().",  # naive local time
    "datetime.now()\n",
)


def test_utcnow_is_tz_aware():
    assert utcnow().tzinfo is not None


def test_created_and_timestamp_mixin_defaults_are_aware():
    # The default_factory is what every table inherits for created_at.
    created_default = CreatedMixin.model_fields["created_at"].default_factory
    assert created_default().tzinfo is not None
    # TimestampMixin inherits the same created_at factory.
    assert (
        TimestampMixin.model_fields["created_at"].default_factory().tzinfo
        is not None
    )


def test_kb_version_edited_at_default_is_aware():
    # The one model that previously wrote a naive edited_at.
    assert (
        KnowledgeBasePageVersion.model_fields["edited_at"].default_factory().tzinfo
        is not None
    )


def test_every_datetime_column_is_timezone_aware():
    """Every mapped DateTime column must be timestamptz (timezone=True). The
    app.models package forces this after registering tables; pin it so a stray
    naive column can't slip through."""
    naive = []
    for table in SQLModel.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, DateTime) and not col.type.timezone:
                naive.append(f"{table.name}.{col.name}")
    assert not naive, f"naive DateTime columns: {naive}"


def test_no_naive_datetime_idioms_in_app_source():
    offenders = []
    for path in _APP_DIR.rglob("*.py"):
        text = path.read_text()
        for needle in _FORBIDDEN:
            if needle in text:
                rel = path.relative_to(_APP_DIR.parent)
                offenders.append(f"{rel}: {needle!r}")
    assert not offenders, (
        "naive datetime idioms found (use common.utcnow() / datetime.now(UTC) and "
        "store the aware value):\n" + "\n".join(offenders)
    )
