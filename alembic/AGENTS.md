# Migration conventions

Scope: `alembic/`. Complements the root `AGENTS.md`.

41 revisions and counting, all against PostgreSQL. Dev, test, and prod run Postgres, so
migrations never need SQLite workarounds and `render_as_batch` is not used.

## How autogenerate sees your models

`env.py` sets `target_metadata = SQLModel.metadata` and does one load-bearing import:

```python
import app.models  # noqa: F401 — registers all models with SQLModel.metadata
```

`app/models/__init__.py` re-exports every table class. **A model not imported there is
absent from the metadata**, so `--autogenerate` will not create it — and, worse, will
emit a `drop_table` for it once it exists in the database. Add the import in the same
commit as the model.

## Commands

```bash
make migration m="add widget table"   # uv run alembic revision --autogenerate -m "..."
make migrate                          # uv run alembic upgrade head
```

**Always read the generated migration before committing it.** Autogenerate does not
detect column renames (it emits drop + add, destroying data), server-default changes, or
`CHECK` constraints, and it is unreliable around native enum alterations.

## Postgres enums need explicit handling

Domain enums subclass `str, Enum` and become native Postgres enum types. **Autogenerate
will not add a value to an existing type** — you must hand-write it:

```python
op.execute("ALTER TYPE functionrunstatus ADD VALUE IF NOT EXISTS 'queued'")
```

`IF NOT EXISTS` is the house idiom (`e1a3c5d7f9b1_expand_function_run.py`); a bare
`ADD VALUE` also appears (`d4e5f6a7b8c9`). Note the Postgres rule this buys you nothing
against: **a newly added enum value cannot be used later in the same transaction.** If a
migration adds a value *and* backfills rows with it, split it across two revisions.
Removing or renaming a value is a type recreation plus a data migration.

## Local dev applies migrations automatically

When `ENVIRONMENT=local`, the `lifespan` in `app/main.py` calls `run_migrations()` before
seeding, so `make dev` works on a fresh checkout without a manual `make migrate`.

`run_migrations()` is deliberately **synchronous** and invoked with `asyncio.to_thread` —
Alembic's `env.py` spins up its own event loop (`asyncio.run`), which would deadlock if
called on the running loop. The `alembic.ini` path resolves absolutely so it works
regardless of cwd. Don't "clean this up" into an async call.

`env.py` supports both modes: online uses `create_async_engine`; offline (`--sql`) strips
the `+asyncpg` suffix via `_sync_url` to emit a plain SQL script.

## Revision files

- Revision ids are arbitrary hand-written slugs, not strictly hex (`r4b7d9e1f3a5_add_plugin_runner.py`).
  Filenames are `<rev>_<snake_case_summary>.py`.
- **There is exactly one head today** (`r4b7d9e1f3a5`). Confirm with `uv run alembic heads`
  before and after adding a revision; two heads means a rebase went wrong.
- One merge revision exists and is legitimate: `k7a0b2c4d6e8_merge_heads.py` joined the
  composite-case-scoped-ids chain to the operational-spine chain. Its `down_revision` is a
  tuple and both `upgrade`/`downgrade` are `pass` — that is the correct shape for a merge.
  Prefer fixing `down_revision` over minting new merges.
- Write a real `downgrade()`. Five of the 41 revisions leave it as `pass`; treat those as
  debt, not precedent. If a migration is genuinely irreversible, raise there with a comment
  explaining why. (A merge revision is the one legitimate `pass`.)
- Data backfills belong in the migration that changes the schema, so a single `upgrade`
  leaves the database consistent. Adding a `NOT NULL` column to a populated table is
  three steps: add nullable, backfill, then alter to `NOT NULL`.

Tests spin a throwaway Postgres via testcontainers (Docker required), so a migration that
only works against your local database will fail in CI.
