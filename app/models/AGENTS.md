# Model conventions

Scope: everything under `app/models/`. Complements the root `AGENTS.md`.

SQLModel table definitions — one file per domain entity. These classes are the single
source of truth for the schema; Alembic autogenerates migrations from them.

## Registration is mandatory

`app/models/__init__.py` imports every table class with `# noqa: F401`. `alembic/env.py`
does `import app.models` purely to populate `SQLModel.metadata`.

**A new table that isn't imported in `__init__.py` is invisible to autogenerate**, and
Alembic will happily emit a migration that drops it. Add the import in the same commit
as the model.

## Mixins (`common.py`)

| Mixin | Adds | Use for |
|---|---|---|
| `CreatedMixin` | `created_at`, `created_by` | Immutable rows, join tables |
| `TimestampMixin` | the above + `updated_at`, `updated_by` | The common case: mutable entities |
| `SoftDeleteMixin` | `deleted_at` (indexed), `deleted_by` | Rows that are flagged, never removed |

`TimestampMixin` extends `CreatedMixin`. Compose soft-delete alongside one of the
other two: `class Case(TimestampMixin, SoftDeleteMixin, table=True)`.

Subclasses that need a non-default author (system- or analyzer-seeded rows) re-declare
just `created_by`.

**`SoftDeleteMixin` is a read-side obligation.** Every query against a soft-deletable
table must filter `deleted_at IS NULL`. Nothing enforces this for you.

Timestamps are stored **naive UTC** — `datetime.now(UTC).replace(tzinfo=None)`. Match
that when writing a timestamp by hand; a tz-aware value will not compare correctly.

## Naming

- `case_.py` / `__tablename__ = "case_"` — the trailing underscore escapes the SQL
  keyword. Polymorphic `object_type` strings strip it back to `case`, which is why
  the Comment/Flag/Tag/Audit convention is the bare word `case`.
- Enums subclass `str, Enum` so they serialize cleanly, and their **values are the wire
  format** (`CaseStatus.open = "Open"`). Renaming a value is a breaking API change and
  needs a data migration.

## Per-case sequence counters

`Case.next_task_seq`, `Case.next_attachment_seq`, and `Task.next_log_seq` are
post-increment counters for composite-keyed children. They are allocated **only** via
`app/crud/_seq.py` — a single `UPDATE … RETURNING` under the row lock the UPDATE
already takes.

Never allocate an id by scanning siblings or taking `MAX(id) + 1`. The counters are
gap-free per scope and never reused, even across soft deletes, because they only move
forward.

## Request/response schemas live beside the table

A model file holds the table class *and* its non-table SQLModel schemas
(`CaseCreate`, `CaseUpdate`, …). Keep them together.

Rich-text fields carry `description=MARKDOWN_NOTE`, which tells the client the value is
CommonMark and **the rendered HTML must be sanitised (e.g. DOMPurify) to prevent stored
XSS.** Apply it to any new markdown-bearing field.

`Page[T]` in `common.py` is the offset-paginated envelope returned by **all** list
endpoints: `items`, `total`, `skip`, `limit`.

## Domain scales

Severity `1=low, 2=medium, 3=high, 4=critical`. TLP/PAP `0=WHITE, 1=GREEN, 2=AMBER
(default), 3=RED`. These integers are mirrored in `catlico-web/src/lib/domain.ts` —
changing one side without the other silently mislabels the UI.

## Multi-tenancy

`CaseShare` grants an Organisation access to a Case and pins the Role for that share.
It is the multi-tenancy engine. Models do not enforce isolation; the queries in
`app/crud/` do. See `app/crud/AGENTS.md`.
