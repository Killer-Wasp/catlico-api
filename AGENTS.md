# Catlico Backend

A security incident case management tool. `docs/thehive4/` contains reference material from TheHive4 — it is used to understand the problem domain, not as a spec to clone or reverse-engineer.

## What this is

Catlico tracks security incidents as **Cases**. Inside cases there are **Tasks**, **Logs**, and **Observables** (IOCs like IPs, domains, URLs, file hashes). **Alerts** are inbound events that can be promoted into Cases. A separate **analyzer engine** uses a plugin model to enrich observables against external/local sources (e.g. MaxMind GeoIP, AbuseIPDB, VirusTotal). Multi-tenancy is enforced via **Organisations**; permissions via **Profiles**.

The TheHive4 docs inform design decisions (data model concepts, permission model, observable types, analyzer I/O contract) but Catlico is its own product — we pick what makes sense and discard the rest. The roadmap in `docs/thehive4/thehive4-roadmap.md` is reference material only.

## Stack

- **Python 3.14**, **FastAPI**, **SQLModel** (SQLAlchemy + Pydantic), **Alembic**
- **PostgreSQL** everywhere — dev, test, and prod all run on Postgres (via Docker) for parity; there is no SQLite fallback. Tenant isolation is enforced in the application layer (query-level `case_share`/`membership` joins), not via Postgres RLS.
- **JWT** (PyJWT) + **bcrypt** (pwdlib) for auth
- **uv** for dependency management; **pytest + pytest-asyncio** for tests

## Project layout

```
app/
  main.py             # FastAPI app, lifespan, router mount
  core/
    configs.py        # Settings (pydantic-settings); DB URL, secrets, env
    db.py             # Async engine + session factory + init_db
    security.py       # JWT creation/verification, password hashing
  models/             # SQLModel table definitions (one file per domain entity)
  crud/               # Database operations (one file per entity, async)
  api/
    deps.py           # FastAPI dependencies (get_db, get_current_user, etc.)
    v1/
      main.py         # v1 APIRouter aggregation
      routes/         # One file per resource group
docs/thehive4/        # Reference docs — data model, parity spec, roadmap, Cortex
alembic/              # Migrations (versions/ has the actual migration files)
tests/                # pytest; conftest.py sets up async test DB
```

## Domain model (target)

The relational schema maps TheHive's graph model to PostgreSQL. Key entities and their relationships:

- **Organisation** — tenant boundary; all visibility flows through it
- **OrganisationMember** — collapses `User → Role → Organisation` into one row (user_id, org_id, role_id)
- **Role / RolePermission** — named permission sets. Permissions are `read:`/`write:` verbs per resource (`read:case`, `write:case`, `read:observable`, `write:alert`, … plus `run:enrichment`); see `app/models/role.py`. Built-in roles: `org-admin`, `analyst`, `read-only`
- **Case** — the central investigation object; auto-incrementing `number`; severity 1–4; TLP/PAP 0–3; status Open|Resolved|Duplicated
- **CaseShare** — grants an Organisation access to a Case and pins the Profile (permission level) for that share. **This is the multi-tenancy engine.** All queries must go through this, not post-filter.
- **Task / Log** — work items and notes within a case
- **Observable** — IOC/artifact; typed by `observable_type` (ip, domain, url, hash, file, mail, etc.); has `ioc` and `sighted` flags; TLP; string value or file attachment
- **Alert** — inbound event unique on (type, source, source_ref, org_id); can be promoted to a Case
- **CaseTemplate** — reusable case/task scaffolding
- **Tag / Taxonomy** — free and MISP-namespaced tags; polymorphic tagging across cases/observables/alerts
- **CustomField** — typed (string|integer|float|boolean|date); polymorphic values across cases/alerts/templates
- **Audit / AuditOutbox** — every mutation writes an audit row + outbox row in the same transaction; outbox dispatches to stream/notification/connector fan-out after commit
- **Pattern / Procedure** — MITRE ATT&CK techniques linked to cases
- **AnalyzerJob / AnalyzerReport** — plugin engine: an observable is submitted to an analyzer worker; the report attaches verdict tags (info|safe|suspicious|malicious) and may extract new observables or trigger case mutations (add tag, create task, etc.)

Full DDL sketch and parity checklist: `docs/thehive4/thehive4-parity-spec.md`

## Current state

Built: auth (JWT login + short-lived access token with a `/auth/refresh` flow that re-reads membership), users, organisations + RBAC (roles, members, org links/auto-share), cases, tasks, logs, observables, alerts (with promotion to case), comments, case templates, tags, flags, connectors + the enrichment/analyzer engine, file attachments (fsspec-backed blob storage), and the **Audit / AuditOutbox** write path + drain (see below).

**Audit / AuditOutbox** (`app/crud/audit.py`, `app/models/audit.py`, `app/core/context.py`; plan: `docs/audit-outbox-plan.md`): `record_audit()` writes one audit row + one outbox row in the mutation's transaction. Wired into case/task/log/observable/comment/alert create/update/delete (in CRUD) and user/org/member/role create/update/delete (in the admin routes, where the acting user is available and `init_db` seeding stays unaudited). Case-scoped children carry `context=case`; alert promotion and admin entities carry `context=case`/`organisation` respectively. A `request_id` contextvar (set by `RequestIdMiddleware`) correlates a request's rows. A lifespan poller drains the outbox post-commit with an **empty consumer registry** (the seam for stream/notification/connector fan-out). Read via `GET /cases/{id}/activity` (gated by `read:case`). Polymorphic targets are string `(object_type, object_id)` — `object_type` strips the `case_` keyword-escape underscore to `case`, matching the Comment/Flag/Tag convention. **Still to wire:** cascade child rows; real outbox consumers; a global/admin audit-search API (admin-entity audits aren't reachable through the case feed).

Not built yet (in the target domain model but absent from code): **CustomField** and **Pattern / Procedure** (MITRE ATT&CK) linkage.

## Development

```bash
# Install deps
uv sync

# Run dev server (needs Postgres — run `make db` first)
uvicorn app.main:app --reload

# Run tests
uv run pytest

# New migration after model changes
alembic revision --autogenerate -m "description"
alembic upgrade head
```

PostgreSQL is required: set `POSTGRES_SERVER`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` in `.env` (or `DATABASE_URL` directly), or run `make db` to start the docker-compose Postgres. Startup fails fast if no database is configured. Tests spin a throwaway Postgres via testcontainers (Docker required). Tenant isolation is enforced in application queries (every read/write goes through a `case_share`/`membership` join), not Postgres RLS.

## Key conventions

- Every table gets `created_at`, `created_by`, `updated_at`, `updated_by` metadata columns.
- Multi-tenancy is enforced inside every query via `case_share`/`membership` joins — never as a post-filter.
- Audit + outbox rows are written in the **same transaction** as the mutation; outbox is dispatched only after the transaction commits.
- TLP/PAP values: 0=WHITE, 1=GREEN, 2=AMBER (default), 3=RED. Severity: 1=low, 2=medium, 3=high, 4=critical.
- The analyzer plugin interface: a worker receives `{dataType, data, tlp, pap, config}` and returns `{full, summary (taxonomies), artifacts (new observables), operations (case mutations)}`.
- API is clean v1 only — no v0/TheHive wire-format compatibility needed.
