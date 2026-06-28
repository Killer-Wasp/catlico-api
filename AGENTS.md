# Catlico Backend

## Collaboration Principles

- Ask, don't assume. If something is unclear, ask before writing a single line. Never make silent assumptions about intent, architecture, or requirements. When running unattended, pick the most reasonable interpretation, proceed, and record the assumption rather than blocking.
- Implement the simplest solution for simple problems, and better solutions for harder problems. Do not over-engineer or add flexibility that is not needed yet.
- Do not touch unrelated code. Surface bad code or design smells you discover so they can be addressed as separate issues.
- Flag uncertainty explicitly. If unsure, ask before proceeding. When useful, conduct a small, localized, low-risk experiment, then bring the hypothesis and results back for discussion. Confidence without certainty causes more damage than admitting a gap.
- Suggest better approaches when they would improve the work, especially when they have a longer-lasting impact than a tactical change.

A security incident case management tool. `docs/thehive4/` contains reference material from TheHive4/Cortex and TheHive 5. Use it to understand the problem domain and upstream design trade-offs, not as a spec to clone or reverse-engineer.

## What this is

Catlico tracks security incidents as **Cases**. Inside cases there are **Tasks**, **Logs**, **Comments**, **Attachments**, and **Observables** (IOCs like IPs, domains, URLs, file hashes). **Alerts** are inbound events that can be promoted or merged into Cases. A separate **analyzer engine** uses a plugin model to enrich observables against external/local sources (for example MaxMind GeoIP, AbuseIPDB, VirusTotal). Multi-tenancy is enforced via **Organisations**; permissions via **Roles** and per-case **CaseShare** role pins.

The TheHive docs inform design decisions (data model concepts, permission model, observable types, analyzer/responder I/O contract) but Catlico is its own product. Pick what makes sense and discard the rest. The roadmap in `docs/thehive4/thehive4-roadmap.md` is reference material only.

## Stack

- **Python 3.14**, **FastAPI**, **SQLModel** (SQLAlchemy + Pydantic), **Alembic**
- **PostgreSQL** everywhere — dev, test, and prod all run on Postgres (via Docker) for parity. Tenant isolation is enforced in the application layer (query-level `case_share`/`membership` joins), not via Postgres RLS.
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
    internal/          # Worker-facing analyzer endpoints
docs/thehive4/        # Reference docs — data model, parity spec, roadmap, Cortex
alembic/              # Migrations (versions/ has the actual migration files)
tests/                # pytest; conftest.py sets up async test DB
```

## Domain model (target)

The relational schema maps TheHive's graph model to PostgreSQL. Key entities and their relationships:

- **Organisation** — tenant boundary; all visibility flows through it
- **OrganisationMember** — collapses `User → Role → Organisation` into one row (user_id, org_id, role_id)
- **Role / RolePermission** — named permission sets. Permissions are `read:`/`write:`/`run:` verbs per resource (`read:case`, `write:case`, `read:observable`, `write:alert`, `run:enrichment`, `run:function`, etc.); see `app/models/role.py`. Built-in roles: `org-admin`, `analyst`, `read-only`
- **Case** — the central investigation object; auto-incrementing `number`; severity 1–4; TLP/PAP 0–3; status Open|Resolved|Duplicated
- **CaseShare** — grants an Organisation access to a Case and pins the Role for that share. **This is the multi-tenancy engine.** All case-scoped queries must go through this, not post-filter.
- **Task / Log** — work items and notes within a case
- **Observable** — IOC/artifact; typed by `observable_type` (ip, domain, url, hash, file, mail, etc.); has `ioc` and `sighted` flags; TLP; string value or file attachment
- **Alert** — inbound event unique on (type, source, source_ref, org_id); can be promoted to a Case
- **CaseMerge** — records case-to-case merges; merged source cases become read-only Duplicated lineage tombstones
- **CaseTemplate** — reusable case/task scaffolding
- **Tag / Taxonomy** — free and MISP-namespaced tags; polymorphic tagging across cases/observables/alerts
- **CustomField** — typed (string|integer|float|boolean|date); org-scoped definitions with polymorphic values across cases and alerts
- **ApiKey / SlaPolicy / KnowledgeBasePage / Function / Notifier / NotificationRule** — web-facing org admin/settings features; some execution/delivery paths are still deferred
- **Audit / AuditOutbox** — every mutation writes an audit row + outbox row in the same transaction; outbox dispatches to stream/notification/connector fan-out after commit
- **Pattern / Procedure** — MITRE ATT&CK techniques linked to cases
- **AnalyzerJob / AnalyzerReport** — plugin engine: an observable is submitted to an analyzer worker; the report attaches verdict tags (info|safe|suspicious|malicious) and may extract new observables or trigger case mutations (add tag, create task, etc.)

Full DDL sketch and parity checklist: `docs/thehive4/thehive4-parity-spec.md`

## Current state

Built:

- Auth: JWT login, persisted refresh tokens, and `/auth/refresh` that re-reads membership.
- Users, organisations, roles/RBAC, org members, and organisation links/auto-share.
- Cases, case shares, case merge, tasks, task queue, logs, observables, observable types, alerts, comments, flags, tags, case templates, attachments, custom fields, and MITRE Pattern/Procedure linkage.
- Alert promotion and alert-to-case merge paths.
- Connectors plus manual observable enrichment: public enqueue/read endpoints and internal analyzer worker register/claim/result endpoints.
- Partial responder plumbing: `ConnectorType.responder`, internal responder routes, and `connector_operations` operation application service exist, but responder jobs/connectors are not yet fully productized.
- Web-facing admin/settings backends: API keys, SLA policies, knowledge base pages, functions CRUD/run records, notifiers, and notification rules.
- Audit and outbox write path, case activity feed, and global superadmin audit search.

**Audit / AuditOutbox** (`app/crud/audit.py`, `app/models/audit.py`, `app/core/context.py`; plan: `docs/audit-outbox-plan.md`): `record_audit()` writes one audit row + one outbox row in the mutation's transaction. Case-scoped children carry `context=case`; alert promotion and admin entities carry `context=case`/`organisation` respectively. A `request_id` contextvar (set by `RequestIdMiddleware`) correlates a request's rows. A lifespan poller drains the outbox post-commit with an **empty consumer registry**. Read case activity via `GET /cases/{id}/activity` (gated by `read:case`) and global audit via `GET /audit/` (superadmin only). Polymorphic targets are string `(object_type, object_id)`; `object_type` strips the `case_` keyword-escape underscore to `case`, matching the Comment/Flag/Tag convention.

Still deferred or partial:

- Real outbox consumers for stream/notification/connector fan-out are not registered yet.
- Notification rules and notifiers have CRUD and delivery data models, but no complete production delivery/test-send path.
- Functions have CRUD, run records, manual run/list-runs/toggle APIs, and permission checks, but no sandboxed execution worker or scoped function-token runtime yet.
- API keys have CRUD and hashing, but request authentication still uses JWT only.
- Analyzer/responder operations plumbing is partial: operation schemas and an application service exist, but responder job queueing, real responder connectors, confirmation UX, and full integration tests are not complete.

## Development

```bash
# Install deps
uv sync

# Run dev server (needs Postgres — run `make db` first)
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# New migration after model changes
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

PostgreSQL is required: set `POSTGRES_SERVER`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` in `.env` (or `DATABASE_URL` directly), or run `make db` to start the docker-compose Postgres. Startup fails fast if no database is configured. Tests spin a throwaway Postgres via testcontainers (Docker required). Tenant isolation is enforced in application queries through active-org membership, org-scoped filters, and case-scoped `case_share` checks, not Postgres RLS.

## Key conventions

- Mutable domain tables generally use `TimestampMixin` (`created_at`, `created_by`, `updated_at`, `updated_by`); immutable rows and join tables use narrower mixins or explicit columns.
- Multi-tenancy is enforced inside queries via active-org membership, org-scoped filters, and case-scoped `case_share` joins — never as a post-filter.
- Audit + outbox rows are written in the **same transaction** as the mutation; outbox is dispatched only after the transaction commits.
- TLP/PAP values: 0=WHITE, 1=GREEN, 2=AMBER (default), 3=RED. Severity: 1=low, 2=medium, 3=high, 4=critical.
- The analyzer plugin interface: a worker receives `{dataType, data, tlp, pap, config}` and returns `{full, summary (taxonomies), artifacts (new observables), operations (case mutations)}`.
- API is clean v1 only — no v0/TheHive wire-format compatibility needed.

## Project docs

- `docs/thehive4/` is source/reference material for TheHive/Cortex and product design context.
- `docs/audit-outbox-plan.md` explains the audit/outbox implementation and deferred fan-out seams.
- `docs/case-merge-design.md` explains case and alert merge semantics.
- `docs/backend-gap-roadmap.md` and `docs/blocked-features-schema-plan.md` are planning/status docs. Check the live code before treating them as current, because implementation may have moved ahead.

When documentation and code disagree, treat the code, migrations, and tests as the source of truth, then update the stale doc.
