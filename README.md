# Catlico API

**The backend for Catlico — an open-source security incident response platform.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)

Catlico tracks security incidents as **cases**. Inside a case live tasks, logs, comments,
attachments, and **observables** — the IPs, domains, URLs, and file hashes that make up an
investigation. **Alerts** stream in from your detection stack and get promoted or merged into
cases. A **plugin system** enriches observables against external intelligence sources without
ever giving plugin code access to your database.

This repository is the API: the control plane, and the only service that holds database
credentials.

## Features

- **Case management** — cases, tasks, logs, comments, attachments, templates, and merge with
  full lineage
- **Observables and IOCs** — typed artifacts with TLP/PAP handling, IOC and sighted flags
- **Alert triage** — deduplicated inbound alerts, promotable to cases
- **Multi-tenancy** — organisations, roles, and per-case sharing that pins the role for each share
- **Full audit trail** — every mutation writes an audit row in the same transaction, with a
  transactional outbox for reliable event fan-out
- **Plugin system** — sandboxed third-party enrichment; plugins propose canonical edits for
  analyst approval rather than making them
- **MITRE ATT&CK** — patterns and procedures linked to cases
- **Live updates** — WebSocket broadcast, notification rules, Webhook and Slack notifiers

## Quick start

Requires **Python 3.14+**, **[uv](https://docs.astral.sh/uv/)**, and **Docker**.

```bash
git clone https://github.com/jimmyruann/catlico-backend.git catlico-api
cd catlico-api

uv sync
cp .env.example .env

# SECRET_ENCRYPTION_KEY is required — startup fails without a valid Fernet key
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# ...paste it into .env

make dev
```

This starts Postgres and a SeaweedFS blob store in Docker, applies migrations, seeds demo
data, and serves the API on **`127.0.0.1:8000`**. Cold start takes 15–20 seconds.

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs   # expect 200
```

Interactive API docs are at **[localhost:8000/docs](http://localhost:8000/docs)**. The seeded
login is `admin@example.com` / `changeme`.

Full walkthrough, including troubleshooting: **[docs/getting-started.md](docs/getting-started.md)**.

## Architecture

```
                    ┌──────────────┐
                    │ catlico-web  │  React SPA
                    └──────┬───────┘
                           │ /api/v1  (JWT or API key)
                    ┌──────▼────────────────────────┐
                    │        catlico-api            │
                    │  the only service with DB     │────► PostgreSQL
                    │  credentials                  │────► blob store (S3 / local)
                    └──┬──────────────────────┬─────┘
       /api/internal/  │                      │  /api/internal/analyzer
       plugin-runner   │                      │
                ┌──────▼──────────┐    ┌──────▼─────────┐
                │ plugin-runner   │    │ konnect        │
                │ sandboxes       │    │ legacy worker  │
                │ plugins         │    │ ~35 connectors │
                └─────────────────┘    └────────────────┘
```

Three API surfaces, separated by who authenticates and how — a caller on one can never reach
another's routes:

| Surface | Prefix | Principal |
|---|---|---|
| Public | `/api/v1/*` | User JWT, or an API key (`thp_…`) |
| Runner | `/api/internal/plugin-runner/*` | Machine credential (`cpr_…`) |
| Runtime | `/api/internal/plugin-runtime/*` | Short-lived per-run token |

Multi-tenancy is enforced **inside every query** through `CaseShare` joins and active-org
membership — never as a post-filter. See **[docs/architecture.md](docs/architecture.md)**.

## Documentation

| Doc | What's in it |
|---|---|
| [Getting started](docs/getting-started.md) | Local setup, everyday commands, migrations, troubleshooting |
| [Architecture](docs/architecture.md) | Layers, domain model, multi-tenancy, auth, the audit/outbox path |
| [Configuration](docs/configuration.md) | Every environment variable, plus a production checklist |
| [Plugin system](docs/plugin-system.md) | Runner and runtime surfaces, dispatch, proposed actions, invariants |
| [Audit & outbox](docs/audit-outbox-plan.md) | Design of the transactional outbox |
| [Case merge](docs/case-merge-design.md) | Case and alert merge semantics |

Contributors and AI agents: [`AGENTS.md`](AGENTS.md) holds the working conventions, with
per-directory notes under `app/models/`, `app/crud/`, `app/api/`, `app/services/`, and `alembic/`.

## Related repositories

| Repo | Role |
|---|---|
| [catlico-web](https://github.com/jimmyruann/catlico-web) | React SPA |
| [catlico-plugin-runner](https://github.com/Killer-Wasp/catlico-plugin-runner) | Sandboxes and executes plugins. No database access. |
| [catlico-plugin-sdk](https://github.com/Killer-Wasp/catlico-plugin-sdk) | The plugin authoring contract |
| [catlico-konnect](https://github.com/Killer-Wasp/catlico-konnect) | Legacy connector worker — **still the production enrichment path** |

## Status

Catlico is under active development. The plugin system runs alongside the legacy connector
worker rather than replacing it yet, and several subsystems are deliberately incomplete —
functions have no execution sandbox, and responder execution is partial. Known gaps are listed
in [docs/architecture.md](docs/architecture.md#known-gaps) rather than hidden.

When documentation and code disagree, the code, migrations, and tests are the source of truth.

## Contributing

Issues and pull requests are welcome. Run `make test` before opening a PR — the suite spins a
throwaway Postgres via testcontainers, so Docker must be running.

## License

[GNU Affero General Public License v3.0](LICENSE).
