# Backend Gap Analysis & Implementation Roadmap

Cross-repo inventory of what is **missing and needs implementing** to reach full
platform completeness, spanning `catlico-api` (this repo) and `catlico-konnect`
(the connector/analyzer worker), plus the api↔konnect enrichment seam.

Companion docs:
- `catlico-web/docs/api-wiring-audit.md` — the frontend stub audit that motivated this.
- `catlico-konnect/docs/gap-analysis.md` — the konnect-side detail (responders, blob, porting).
- `docs/blocked-features-schema-plan.md` — the schema-layer plan for Workstream A models + migrations (all DONE).

> Last updated: 2026-06-28 — aligned with remediation review.

Findings are concrete: `catlico-api`'s `app/` is clean (no half-finished TODOs),
so "missing" means features with no crud/route/migration — not broken code.
Workstream A models are written; see [blocked-features-schema-plan.md](blocked-features-schema-plan.md).

---

## Already built — do NOT rebuild

- `GET/PATCH users/me` (`app/api/v1/routes/users.py`); CustomFields (model+crud+route, mounted in `app/api/v1/main.py`); full case/alert/task/log/observable/comment/template/role/org CRUD; audit write-path + outbox **drain**; connectors list/config/test/enable; **manual** enrichment.
- **MITRE ATT&CK Pattern / Procedure** — models, routes, and case linkage exist (`app/api/v1/routes/patterns.py`).
- **Notification feed + delivery** — outbox consumers, user notification feed (`GET/PATCH notifications/`), webhook/Slack notifier delivery all implemented.
- **Outbox consumers** — registered and operational: notification feed, notifier dispatch, WebSocket stream.
- **WebSocket activity stream** — `/api/v1/activity` endpoint + `websocket_hub` exist.
- **API-key auth** — CRUD (`/api/v1/api-keys/`) + request authentication wired. 14 programmatic routes accept API keys via `ActiveOrgOrApiKeyContext`. Org-admin routes (api-keys, notifications, organisations) remain JWT-only.
- **Enrichment automation** — auto-enqueue on JSON case/alert observable creation (B2: mostly done; file observable endpoints not yet enqueuing). Verdict rollup (B3), artifact provenance (B4), konnect periodic register (B5) done.
- **Event envelope + outbox contract** (A1) done.
- **Responder scaffold** — source catalog (E0), partial API + Konnect SDK scaffold (E1/E2). End-to-end integration (E3) pending.
- **Enrichment (manual path, end-to-end)**: konnect registers manifests at startup → `POST observables/{id}/enrich` enqueues → worker claims via `internal/analyzer/work` (SELECT…FOR UPDATE SKIP LOCKED, leases) → posts to `…/result` → API stores report, replaces `ReportTag`s, imports extracted artifacts as case observables.
- **Observable types** — `observable_types.py` route (GET/POST/DELETE) mounted; `ObservableType` model + `BUILTIN_OBSERVABLE_TYPES` seed in `app/models/observable.py`.
- **Organisation links** — `GET/POST/PATCH/DELETE organisations/{org_id}/links` live inside `app/api/v1/routes/organisations.py`; model + crud in `organisation_link.py`.
- **Global admin audit search** — `GET /audit/` (SuperAdmin-only, paginated, filterable by action/object_type/context) in `app/api/v1/routes/audit.py`.
- **Models + CRUD/routes**: KnowledgeBasePage, ApiKey, SlaPolicy, Notifier, NotificationRule models + CRUD + routes + mounts are DONE (Workstream A). See [blocked-features-schema-plan.md](blocked-features-schema-plan.md) for migration history. (Function was also built here but removed 2026-07 — superseded by the plugin system.)
- **Custom metrics — ❌ REMOVED 2026-07-14** — redundant with custom fields (mirrors TheHive's own deprecation of case metrics). `/metrics` CRUD + per-case metric values dropped (`metric`/`case_metric_value` tables, migration `a9d1e3f5b7c9`); dashboards are unaffected — they read `/overview`, never metrics. Do not rebuild; use custom fields.

---

## Workstream A — Web-facing feature backends ✅ DONE

CRUD, routes, and mounts are **complete** for all six entities. Models, migrations,
and API endpoints are live:

| Feature | Endpoints | Powers web stub | Status |
|---|---|---|---|
| Functions / automation | ~~`functions/` CRUD + toggle + run~~ | ~~FunctionsPage~~ | ❌ REMOVED 2026-07 — superseded by the plugin system (never executed real code) |
| Knowledge Base | `GET/POST/PATCH/DELETE knowledge-base/` (pages w/ block content) | KnowledgeBase | ✅ DONE |
| API keys | `GET/POST api-keys/`, `DELETE api-keys/{id}` | Settings → ApiKeysPanel | ✅ CRUD + request auth done (P0.1 remediated 2026-06-28) |
| SLA policies | `GET sla-policies/`, `PUT sla-policies/` (bulk upsert) | Settings → SlaPanel | ✅ DONE |
| Notification rules + Notifiers | `GET/PATCH notification-rules/`, `GET/POST/PATCH notifiers/` | Settings → NotificationsPanel | ✅ CRUD + delivery done (webhook/slack) |
| User Notifications feed | `GET notifications/`, `PATCH notifications/{id}`, `POST notifications/read-all` | Header notifications bell | ✅ DONE (outbox consumers operational) |

> Workstream A CRUD is complete. Remaining sub-items are **execution/delivery**
> behavior (notifier dispatch, API-key auth, notification feed),
> not CRUD scaffolding. These are tracked in the current milestone plan below.

---

## Workstream B — Taxonomies/freetags management (partial)

- **Taxonomies / freetags management** — `app/models/tag.py` model exists; `app/api/v1/routes/tags.py` only exposes `GET /tags/` (list, filtered by namespace) and admin `DELETE /tags/{tag_id}`. Missing: create/update taxonomy rows (`POST/PATCH taxonomies/`), freetag CRUD (`GET/POST freetags/`), distinct from per-entity tagging. → Settings → TaxonomiesPanel.

---

## Workstream C — Domain-model completeness

- **MITRE ATT&CK Pattern / Procedure** — ✅ DONE. Models, routes, and case linkage exist at `app/api/v1/routes/patterns.py`.
- **Audit outbox consumers** — ✅ DONE. Consumers registered and operational for notification fan-out, notifier dispatch, and WebSocket stream.
- **Cascade child audit rows** — on parent soft-delete, child audit rows orphan (AGENTS.md "still to wire"). Add cascade in the delete CRUD paths.

---

## Workstream D — Enrichment pipeline automation (api↔konnect)

Manual enrich works; automation does not.

- **Auto-enrich on creation** — ✅ Mostly done. JSON case/alert observable creation enqueues; file observable endpoints do not enqueue yet.
- **Observable verdict rollup** — ✅ DONE. `Observable.verdict` recomputed on result ingestion.
- **Periodic catalog re-sync** — ✅ DONE. Konnect periodic register implemented.
- **Artifact lineage** — ✅ DONE. Provenance persisted with connector context.

---

## Workstream E — Responder capability (cross-cutting: api + konnect SDK)

The largest net-new capability. Today everything is analyzer-first; responder
scaffolding is partial (source catalog done, API route stubbed, Konnect worker
branch exists but not in main loop).

- **api**: activate `ConnectorType.responder`; build responder job lease/claim/result endpoints (currently stubbed at `/api/internal/responder/work`); add `operations` to `ResultSubmit` (`app/models/enrichment.py`) and an ingestion path that **applies case mutations** (add-tag, create-task, change-tlp, …) safely under tenancy.
- **konnect/sdk**: finish `Responder` base class + `ActionResult` model; wire responder polling in worker main loop; client endpoints for responder claim/submit.
- **web**: the Connectors page already models `kind: analyzer | responder` — it lights up once the backend supports it.

---

## Workstream F — konnect connector coverage (ongoing)

See `catlico-konnect/docs/gap-analysis.md`. Current: **40 of 275 flavors done**
(A=26/35, B=14/188, C=0/35, D=0/17). Net-new milestones that unblock many at once:

- **File/blob input seam** (unblocks 35 Category C) — needs a backend blob milestone so leases pass file refs (blob id/URL) into konnect's `WorkInput` instead of raw bytes.
- **Local-tool/daemon worker model** (17 Category D) — needs a deployment decision (sidecar/host-mount/second worker type).
- **Porting backlog** (235 todo) — ongoing; prioritize high-value B analyzers and finish partial ports (VirusTotal Scan/Rescan, Shodan/Robtex/Urlscan variants).

---

## Current milestone order (post-remediation 2026-06-28)

Items marked ✅ are done; ❌ are remaining.

1. ✅ Operational Spine — outbox consumers, notification feed, notifier delivery, WebSocket stream
2. ✅ Enrichment Automation — auto-enrich, verdict rollup, artifact provenance, konnect periodic register
3. ✅ API-Key Auth — request authentication with API keys (wired to 14 programmatic routes)
4. ~~Functions Runtime~~ — DROPPED 2026-07: feature removed, plugins are the extensibility mechanism
5. ❌ Responders — action connectors, operation schema, konnect responder SDK (P1.2)
6. ❌ File/Blob Analyzer Leases — file_ref in work items (P1.1)
7. ❌ Threat Intel Depth — MISP import/export stubs (P1.4), local-tool worker mode (P2.3)
8. ❌ Analytics/Auth Hardening — password reset delivery (P2.1), OIDC/SAML (P2.2)

## Suggested phasing (superseded by above)

<details>
<summary>Historical phasing plan — kept for reference</summary>

1. **Taxonomies CRUD (B)** — close out the last route-only gap. Unblocks Settings → TaxonomiesPanel.
2. **Web feature backends (A: API keys, SLA, Knowledge Base)** — independent CRUD; models already written. Fast sequential wins.
3. **Outbox spine + notifications (C consumers + A notifications/notifiers/rules + user feed)** — build consumers first, then the user feed and notifier dispatch on top. Lights up the Header bell + Notifications settings together.
4. **Functions (A)** — depends on a sandbox; reuse konnect isolation. Larger.
5. **Enrichment automation (D)** — auto-enrich, verdict rollup, catalog re-sync.
6. **ATT&CK (C Pattern/Procedure)** + **Responders (E)** — deepest, cross-repo.
7. **Ongoing — konnect coverage (F)** — blob seam, then porting, in parallel.

</details>

---

## Verification

- **Pattern conformance**: each new feature mirrors `custom_field.py` (model/crud/route), is mounted in `app/api/v1/main.py`, has an Alembic migration (`alembic revision --autogenerate`), and enforces tenancy via membership/`case_share` joins — never post-filter. `uv run pytest` (testcontainers Postgres).
- **Outbox consumers**: write a mutation → assert audit + outbox row in the same txn, and that the registered consumer fires post-commit (notification row created / notifier called).
- **Auto-enrich**: create an observable on a case → assert enrichment jobs enqueued for matching enabled connectors; run konnect against the dev API and confirm `ReportTag`s + verdict rollup land.
- **Responders**: register a responder connector in konnect, claim a responder job, submit operations, assert the case mutation applied and audited.
- **End-to-end web**: with this API (`make db` + uvicorn) and `catlico-web` (`VITE_API_BASE_URL=…:8000/api/v1`), each previously-stubbed panel/page now loads real data, performs create/update/delete, and persists across refresh.
