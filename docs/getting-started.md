# Getting started

Set up `catlico-api` for local development.

For the full multi-service stack (API + worker + web), see `DEVELOPMENT.md` in the
[catlico](https://github.com/jimmyruann) workspace root.

## Prerequisites

- **Python 3.14+**
- **[uv](https://docs.astral.sh/uv/)** — dependency management
- **Docker** — Postgres, the SeaweedFS blob store, and the test suite's throwaway database

PostgreSQL is the only supported engine. Dev, test, and production all run on it, so there
are no SQLite-compatibility branches to maintain.

## Setup

```bash
git clone https://github.com/jimmyruann/catlico-backend.git catlico-api
cd catlico-api

uv sync                 # install dependencies
cp .env.example .env    # then edit — see below
make dev
```

### Required before first run

`SECRET_ENCRYPTION_KEY` must be a valid Fernet key or **startup fails immediately**. Generate one:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put it in `.env`. Everything else has a working local default — see
[configuration.md](configuration.md).

## What `make dev` does

1. `docker compose up -d db seaweedfs` — Postgres 16 on `:5432`, a SeaweedFS S3 gateway on `:8333`.
2. Exports local Postgres defaults (`catlico`/`catlico`/`catlico`) if `.env` doesn't set them.
3. `uv run uvicorn app.main:app --reload` on **`127.0.0.1:8000`**.

On startup the lifespan (`app/main.py`):

- Validates `SECRET_ENCRYPTION_KEY` is a valid Fernet key — hard failure otherwise.
- When `ENVIRONMENT=local`, applies **Alembic migrations to head** automatically, so a fresh
  checkout needs no manual `make migrate`.
- Seeds built-in roles, observable types, the default superadmin, and local demo data.
- Registers outbox consumers and starts four background loops.

**Cold start takes 15–20 seconds** (migrations + seeding).

## Confirming it's up

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs   # expect 200
```

Check `/docs` rather than tailing logs. Two log lines look alarming and are not:

- `Waiting for application startup` can appear to hang. It has usually already finished.
- The outbox poller retry-logs `user_notification` foreign-key violations. These come from
  stale seed data and are **harmless background noise**, not a startup failure.

**Seeded dev login:** `admin@example.com` / `changeme` (from `DEFAULT_ADMIN_EMAIL` /
`DEFAULT_ADMIN_PASSWORD`).

**Interactive docs:** `/docs`, `/redoc`, `/openapi.json` — all disabled when
`ENVIRONMENT=production`.

## Everyday commands

```bash
make dev                      # Postgres + SeaweedFS + reloading uvicorn
make db                       # just the containers
make test                     # pytest; spins a throwaway Postgres via testcontainers
make migrate                  # alembic upgrade head
make migration m="add thing"  # alembic revision --autogenerate
```

Tests need Docker: `testcontainers` starts a real Postgres per run, so a migration that only
works against your local database will still fail in CI.

## Making a schema change

1. Edit or add the model under `app/models/`.
2. **Import the table class in `app/models/__init__.py`.** Alembic's `env.py` imports
   `app.models` purely to populate `SQLModel.metadata`. A model that isn't imported there is
   invisible to autogenerate — and worse, autogenerate will emit a `drop_table` for it once
   the table exists.
3. `make migration m="describe the change"`.
4. **Read the generated migration.** Autogenerate does not detect column renames (it emits
   drop + add, destroying data), server-default changes, or `CHECK` constraints, and it is
   unreliable around native Postgres enum alterations.
5. `make migrate`.

See [`alembic/AGENTS.md`](../alembic/AGENTS.md) for the enum and merge-revision conventions.

## Troubleshooting

**`SECRET_ENCRYPTION_KEY is not a valid Fernet key`** — regenerate it with the command above.
It must be a 32-byte urlsafe-base64 key, not an arbitrary string.

**Startup fails with no database configured** — set `POSTGRES_SERVER`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB` (or `DATABASE_URL` directly), or just run `make db`. The
service fails fast rather than falling back to SQLite.

**SeaweedFS keeps restarting** — check `docker logs catlico-api-seaweedfs-1`. The image's
entrypoint always prepends `weed`, which is why `docker-compose.yml` sets `entrypoint: []`
and uses a literal `|` block scalar for the command. Don't "simplify" either back.

**Tests fail to start** — Docker isn't running, or the daemon isn't reachable. `testcontainers`
needs it.

## Where to go next

- [architecture.md](architecture.md) — request lifecycle, multi-tenancy, the audit/outbox path
- [configuration.md](configuration.md) — every environment variable
- [plugin-system.md](plugin-system.md) — the plugin runner, runtime, and proposed actions
- [`AGENTS.md`](../AGENTS.md) — conventions, plus per-directory notes under `app/`
