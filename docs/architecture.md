# Architecture

`catlico-api` is the **control plane** and the **only service with database credentials**.
Everything that touches the database goes through it: the web app, API-key clients, the
legacy connector worker, and the plugin system.

## Layers

```
app/
  main.py       FastAPI app, lifespan, router mount, background pollers
  core/         Settings, async engine + session factory, JWT/password hashing
  models/       SQLModel tables — the schema source of truth
  crud/         Async database operations; where multi-tenancy is enforced
  services/     Cross-cutting orchestration, outbox consumers, background sweeps
  api/
    deps.py     Every FastAPI dependency: auth, permissions, session
    v1/         Public routes (browsers, API keys)
    internal/   Machine-principal routes (workers, plugin runners, plugin runs)
```

Dependencies point one way: `api → services → crud → models`. **CRUD never imports a service.**

Each of these directories carries an `AGENTS.md` with its local conventions.

## Domain model

Catlico tracks security incidents as **Cases**. Inside a case: **Tasks**, **Logs**,
**Comments**, **Attachments**, and **Observables** (IOCs — IPs, domains, URLs, hashes, files).
**Alerts** are inbound events that can be promoted or merged into cases.

| Entity | Role |
|---|---|
| `Organisation` | Tenant boundary; all visibility flows through it |
| `OrganisationMember` | Collapses `User → Role → Organisation` into one row |
| `Role` / `RolePermission` | Named permission sets. Built-ins: `org-admin`, `analyst`, `read-only` |
| `Case` | The central investigation object. Auto-incrementing `number`; severity 1–4; TLP/PAP 0–3 |
| `CaseShare` | Grants an org access to a case and **pins the role** for that share |
| `Task` / `Log` | Work items and notes within a case |
| `Observable` | Typed IOC with `ioc` / `sighted` flags and its own TLP |
| `Alert` | Inbound event, unique on `(type, source, source_ref, org_id)` |
| `CaseMerge` | Merged source cases become read-only `Duplicated` lineage tombstones |
| `Tag` / `Taxonomy` | Free and MISP-namespaced tags, polymorphic across entities |
| `CustomField` | Typed, org-scoped definitions with polymorphic values |
| `Audit` / `AuditOutbox` | Every mutation writes both, in the mutation's transaction |
| `Pattern` / `Procedure` | MITRE ATT&CK techniques linked to cases |

**Scales.** Severity `1=low, 2=medium, 3=high, 4=critical`. TLP/PAP `0=WHITE, 1=GREEN,
2=AMBER (default), 3=RED`. These integers are mirrored in the web app's `src/lib/domain.ts`.

## Multi-tenancy

`CaseShare` is the multi-tenancy engine. It grants an Organisation access to a Case and pins
the Role that applies *for that share* — so the same case can be visible to two orgs with
different permissions.

> Isolation is enforced **inside the query**, never as a post-filter.

Case-scoped reads join through `CaseShare`; org-scoped reads filter on active-org membership.
Fetching rows and then dropping the ones the caller can't see is a data leak the moment
pagination is involved — the `total` would count rows the caller shouldn't know exist.

Tenant isolation is an application-layer concern here, not Postgres row-level security.

## Authentication

The bearer token's prefix decides which principal you are:

| Prefix | Principal | Surface |
|---|---|---|
| `thp_` | API key — carries its own scopes, never superadmin | `/api/v1` |
| *(JWT)* | User — requires the `X-Organisation-Id` header | `/api/v1` |
| `cpr_` | Plugin-runner machine credential; requires `enrollment_state == "enrolled"` | `/api/internal/plugin-runner` |
| *(opaque)* | Per-run plugin runtime token; dies at terminal status | `/api/internal/plugin-runtime` |

API-key auth is tried first; JWT is the fallback. A JWT request without `X-Organisation-Id`
is a **400**, not a 401. Superadmins receive the full permission set and skip the membership
check.

Permissions are `read:` / `write:` / `run:` verbs per resource (`read:case`, `write:observable`,
`run:enrichment`, …). Declare them as dependencies — `require_permission`,
`require_case_permission`, `require_case_owner` — never as an inline check inside a handler.

`require_case_permission` resolves the case through `CaseShare`, applies the role pinned on
that share, and rejects writes to a `Duplicated` (merged-away) case.

## The request lifecycle

`get_session` is a **unit of work**: one transaction per request, committed once if the
handler returns and rolled back if it raises.

This is what lets a mutation and its audit rows land atomically. Route handlers never call
`session.commit()`, and CRUD functions shouldn't either. (Four older admin-entity CRUD
modules — `user`, `role`, `organisation`, `organisation_member` — still commit inline. That's
known debt, not a pattern to copy.)

`RequestIdMiddleware` sets a `request_id` contextvar so every row a single request writes can
be correlated afterwards.

## Audit and the outbox

Every mutation calls `record_audit()`, which writes **one `Audit` row and one `AuditOutbox`
row in the caller's transaction**. A rolled-back mutation therefore leaves no audit trail.

Outbox rows are dispatched only **after** commit, by a background poller. This is the
transactional-outbox pattern: it guarantees that an event is never published for a change
that didn't commit.

`dispatch_pending_outbox` runs each registered consumer for a row and marks it delivered only
if **all** succeeded. A single failure leaves the row undelivered and the poller retries it,
re-running every consumer — including the ones that already succeeded.

> **Outbox consumers must be idempotent.**

Four consumers are registered at startup:

| Consumer | Does |
|---|---|
| `notify_feed_consumer` | Creates a `UserNotification` per audit event |
| `notifier_delivery_consumer` | Rule matching → Webhook / Slack |
| `ws_broadcast_consumer` | Live broadcast to connected WebSocket clients |
| `plugin_event_consumer` | Enqueues events for healthy plugin runners |

`build_event_envelope()` is the stable event shape every consumer reads.

Read a case's activity via `GET /cases/{id}/activity` (gated by `read:case`), and the global
audit via `GET /audit/` (superadmin only).

## Background loops

Three `asyncio` tasks, started and cancelled by the `lifespan` in `app/main.py`:

| Loop | Interval | Purpose |
|---|---|---|
| `_outbox_poller` | 5s | Drains the audit outbox to registered consumers |
| `_plugin_maintenance_poller` | 30s | Reaper, offline detection, rollups, retention, cron |
| `_plugin_push_poller` | 5s | Pushes queued event deliveries to runners |

Every poller wraps its body in `try/except Exception` and logs. **A poller must never die on a
transient error.** Only `asyncio.CancelledError` propagates.

## Known gaps

- **API keys authenticate but responders don't fully execute.** Responder job queueing and real
  responder connectors are incomplete.
- **`websocket_hub` is in-memory** and does not survive multiple processes. It needs a shared
  backend before horizontal scaling.
- **Notifier rules and notifiers** have CRUD and delivery models but no complete production
  test-send path.

## See also

- [plugin-system.md](plugin-system.md) — the runner, runtime, dispatch, and proposed actions
- [audit-outbox-plan.md](audit-outbox-plan.md) — the design behind the audit/outbox path
- [case-merge-design.md](case-merge-design.md) — case and alert merge semantics
- [backend-gap-roadmap.md](backend-gap-roadmap.md) — planning status; **check the code first**, it may have moved ahead
