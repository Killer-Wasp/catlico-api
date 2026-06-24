# Backend Gap Analysis & Implementation Roadmap

Cross-repo inventory of what is **missing and needs implementing** to reach full
platform completeness, spanning `catlico-api` (this repo) and `catlico-konnect`
(the connector/analyzer worker), plus the api↔konnect enrichment seam.

Companion docs:
- `catlico-web/docs/api-wiring-audit.md` — the frontend stub audit that motivated this.
- `catlico-konnect/docs/gap-analysis.md` — the konnect-side detail (responders, blob, porting).
- `docs/blocked-features-schema-plan.md` — the schema-layer plan for Workstream A models + migrations (models done, migrations pending).

Findings are concrete: `catlico-api`'s `app/` is clean (no half-finished TODOs),
so "missing" means features with no crud/route/migration — not broken code.
Workstream A models are written; see [blocked-features-schema-plan.md](blocked-features-schema-plan.md).

---

## Already built — do NOT rebuild

- `GET/PATCH users/me` (`app/api/v1/routes/users.py`); CustomFields (model+crud+route, mounted in `app/api/v1/main.py`); full case/alert/task/log/observable/comment/template/role/org CRUD; audit write-path + outbox **drain** (consumer registry present but empty); connectors list/config/test/enable; **manual** enrichment.
- **Enrichment (manual path, end-to-end)**: konnect registers manifests at startup → `POST observables/{id}/enrich` enqueues → worker claims via `internal/analyzer/work` (SELECT…FOR UPDATE SKIP LOCKED, leases) → posts to `…/result` → API stores report, replaces `ReportTag`s, imports extracted artifacts as case observables.
- **Observable types** — `observable_types.py` route (GET/POST/DELETE) mounted; `ObservableType` model + `BUILTIN_OBSERVABLE_TYPES` seed in `app/models/observable.py`.
- **Organisation links** — `GET/POST/PATCH/DELETE organisations/{org_id}/links` live inside `app/api/v1/routes/organisations.py`; model + crud in `organisation_link.py`.
- **Global admin audit search** — `GET /audit/` (SuperAdmin-only, paginated, filterable by action/object_type/context) in `app/api/v1/routes/audit.py`.
- **Models without CRUD/routes**: Function, KnowledgeBasePage, ApiKey, SlaPolicy, Notifier, NotificationRule models exist (`app/models/{function,knowledge_base,api_key,sla,notification}.py`). CRUD, routes, and mounts are the remaining work (Workstream A).

---

## Workstream A — Web-facing feature backends (model exists, add crud + route + mount)

Brand-new entities with **models written, zero CRUD/route**. Follow the pattern end-to-end:
`app/crud/<x>.py` → `app/api/v1/routes/<x>.py` → mount in
`app/api/v1/main.py` → Alembic migration. **Use the `custom_field.py` trio as the
reference template.** Standard `created_at/by`, `updated_at/by` columns; tenancy
via membership/org scoping.

| Feature | New endpoints | Powers web stub | Model |
|---|---|---|---|
| **Functions / automation** | `GET/POST/PATCH/DELETE functions/`, `POST functions/{id}/toggle`, `POST functions/{id}/test` (real sandboxed run) | FunctionsPage (local `useState`, fake console) | `app/models/function.py` |
| **Knowledge Base** | `GET/POST/PATCH/DELETE knowledge-base/` (pages w/ block content) | KnowledgeBase (hardcoded pages) | `app/models/knowledge_base.py` |
| **API keys** | `GET/POST api-keys/`, `DELETE api-keys/{id}`; + validate keys in `app/api/deps.py` alongside JWT | Settings → ApiKeysPanel | `app/models/api_key.py` |
| **SLA policies** | `GET sla-policies/`, `PUT sla-policies/` (bulk upsert); breach evaluation hook | Settings → SlaPanel | `app/models/sla.py` |
| **Notification rules + Notifiers** | `GET/PATCH notification-rules/`, `GET/POST/PATCH notifiers/`, `POST notifiers/{id}/test` (Slack/Email/Webhook/Kafka) | Settings → NotificationsPanel | `app/models/notification.py` |
| **User Notifications feed** | `GET notifications/`, `PATCH notifications/{id}`, `POST notifications/read-all` | Header notifications bell | `app/models/notification.py` |

> In the last round, all six models were written up-front (full SQLModel tables,
> API schemas, enums, JSON columns). The remaining work is CRUD + route + mount
> + Alembic migration. Functions "test run" and Notifiers "test" are the deepest
> — they need real execution/delivery, not just CRUD. Functions execution should
> reuse the konnect subprocess-isolation model rather than inventing a second
> sandbox.
>
> **Immediate next step**: create the 5 Alembic migrations so the DB has tables
> for all Workstream A entities. See [blocked-features-schema-plan.md](blocked-features-schema-plan.md)
> for the per-migration spec (indexes, unique constraints, enum types, permission
> backfill). After that, CRUD+route work can proceed table by table.

---

## Workstream B — Taxonomies/freetags management (partial)

- **Taxonomies / freetags management** — `app/models/tag.py` model exists; `app/api/v1/routes/tags.py` only exposes `GET /tags/` (list, filtered by namespace) and admin `DELETE /tags/{tag_id}`. Missing: create/update taxonomy rows (`POST/PATCH taxonomies/`), freetag CRUD (`GET/POST freetags/`), distinct from per-entity tagging. → Settings → TaxonomiesPanel.

---

## Workstream C — Domain-model completeness

- **MITRE ATT&CK Pattern / Procedure** — fully absent (AGENTS.md "not built yet"). New `Pattern`/`Procedure` models + case linkage + route (`GET patterns/`, `PUT cases/{id}/procedures`). → web "ATT&CK matrix" nav item (currently a dead link).
- **Audit outbox consumers** — `register_consumer`/`_consumers` exists but empty in `app/crud/audit.py`; the poller in `app/main.py` drains to nothing. Register real consumers: **notification fan-out** (feeds A's user feed), **notifier dispatch** (Slack/Email/Webhook from A), and a **stream** seam. This is the spine that makes A's notifications real instead of polled tables.
- **Cascade child audit rows** — on parent soft-delete, child audit rows orphan (AGENTS.md "still to wire"). Add cascade in the delete CRUD paths.

---

## Workstream D — Enrichment pipeline automation (api↔konnect)

Manual enrich works; automation does not.

- **Auto-enrich on creation** — observables created via case/alert routes never enqueue jobs (`app/api/v1/routes/cases.py` `create_case_observable` just returns). Add an outbox consumer (or post-create hook) that enqueues all org-enabled connectors matching the observable type, reusing `enrichment_crud.enqueue()` (`app/crud/enrichment.py`).
- **Observable verdict rollup** — verdict lives only per-job; the observable has no rolled-up worst-verdict field. Add `verdict` to `Observable`, recompute on result ingestion in `enrichment_crud.submit_result()` from `ReportTag`s.
- **Periodic catalog re-sync** — konnect only registers manifests at startup; stale until restart. Re-register on an interval / heartbeat.
- **Artifact lineage** — extracted artifacts drop the connector context/`message`; persist provenance.

---

## Workstream E — Responder capability (cross-cutting: api + konnect SDK)

The largest net-new capability. Today everything is analyzer-only; `responder` is
a reserved-but-commented enum in `app/models/connector.py` and the konnect SDK
hardcodes `connector_type = "analyzer"`.

- **api**: activate `ConnectorType.responder`; responder job lease/claim/result endpoints (parallel to the analyzer ones in `app/api/internal/routes/analyzer.py`); add `operations` to `ResultSubmit` (`app/models/enrichment.py`) and an ingestion path that **applies case mutations** (add-tag, create-task, change-tlp, …) safely under tenancy.
- **konnect/sdk**: new `Responder` base class + `ActionResult` model; worker branch for operations; client endpoints for responder claim/submit.
- **web**: the Connectors page already models `kind: analyzer | responder` — it lights up once the backend supports it.

---

## Workstream F — konnect connector coverage (ongoing)

See `catlico-konnect/docs/gap-analysis.md`. Current: **40 of 275 flavors done**
(A=26/35, B=14/188, C=0/35, D=0/17). Net-new milestones that unblock many at once:

- **File/blob input seam** (unblocks 35 Category C) — needs a backend blob milestone so leases pass file refs (blob id/URL) into konnect's `WorkInput` instead of raw bytes.
- **Local-tool/daemon worker model** (17 Category D) — needs a deployment decision (sidecar/host-mount/second worker type).
- **Porting backlog** (235 todo) — ongoing; prioritize high-value B analyzers and finish partial ports (VirusTotal Scan/Rescan, Shodan/Robtex/Urlscan variants).

---

## Suggested phasing

1. **Taxonomies CRUD (B)** — close out the last route-only gap. Unblocks Settings → TaxonomiesPanel.
2. **Web feature backends (A: API keys, SLA, Knowledge Base)** — independent CRUD; models already written. Fast sequential wins.
3. **Outbox spine + notifications (C consumers + A notifications/notifiers/rules + user feed)** — build consumers first, then the user feed and notifier dispatch on top. Lights up the Header bell + Notifications settings together.
4. **Functions (A)** — depends on a sandbox; reuse konnect isolation. Larger.
5. **Enrichment automation (D)** — auto-enrich, verdict rollup, catalog re-sync.
6. **ATT&CK (C Pattern/Procedure)** + **Responders (E)** — deepest, cross-repo.
7. **Ongoing — konnect coverage (F)** — blob seam, then porting, in parallel.

---

## Verification

- **Pattern conformance**: each new feature mirrors `custom_field.py` (model/crud/route), is mounted in `app/api/v1/main.py`, has an Alembic migration (`alembic revision --autogenerate`), and enforces tenancy via membership/`case_share` joins — never post-filter. `uv run pytest` (testcontainers Postgres).
- **Outbox consumers**: write a mutation → assert audit + outbox row in the same txn, and that the registered consumer fires post-commit (notification row created / notifier called).
- **Auto-enrich**: create an observable on a case → assert enrichment jobs enqueued for matching enabled connectors; run konnect against the dev API and confirm `ReportTag`s + verdict rollup land.
- **Responders**: register a responder connector in konnect, claim a responder job, submit operations, assert the case mutation applied and audited.
- **End-to-end web**: with this API (`make db` + uvicorn) and `catlico-web` (`VITE_API_BASE_URL=…:8000/api/v1`), each previously-stubbed panel/page now loads real data, performs create/update/delete, and persists across refresh.
