# Build an Open-Source TheHive 5 Alternative — Implementation Plan

## Context

We want to build an open-source alternative to **TheHive 5** (StrangeBee's closed-source
incident-response / SOAR platform). TheHive 5 is documented-only (no source); it is built on
the **open-source TheHive 4** line (Scala/Play on JanusGraph, via the in-house *ScalliGraph*
graph-ORM), which **is** available as archived source — so this plan diffs *TH5 docs* against
*TH4 source* and targets the **product shape**, not a byte-for-byte clone.

This repo already contains the full **research + design** layer (data model, API internals,
RBAC/sharing, Cortex/MISP deep-dives, a 14-subsystem parity spec, an M0–M10 roadmap, the TH5
feature-gap, docs map/digest, and a **runnable Postgres RLS PoC**). This plan adds the three
things that were missing: (1) a **source-verified gap analysis** (what the real TH4 code has
that our docs under-specified), (2) **what users actually want** (mined from the TheHive/Cortex
GitHub issues), and (3) a **detailed, stack-specific implementation + feature plan** for the
chosen target stack.

**Target stack (decided): Python 3.12 + FastAPI + SQLAlchemy 2.0, on PostgreSQL
(production / multi-tenant) and SQLite (dev / single-org).** Optional OpenSearch for large
full-text deployments.

**Intended outcome:** a single, executable plan that takes us from the existing design to a
working backend, sequenced into milestones, with a must-have feature list grounded in real
demand and a per-feature implementation breakdown on this stack.

> Scope note: this is a large, multi-quarter program (~13–22 months for full TH4+TH5 parity per
> the existing roadmap). This plan is the **whole map**; execution proceeds milestone-by-milestone,
> each with its own implementation pass. A useful internal MVP (M0–M2 + a thin M3 slice + minimal
> UI) is reachable in ~6–7 months.
>
> Relationship to the other docs: [`thehive4-parity-spec.md`](./thehive4-parity-spec.md) is *what
> to build*, [`thehive4-roadmap.md`](./thehive4-roadmap.md) is the *stack-agnostic order*, and
> this doc is *how to build it on the chosen Python/FastAPI/PostgreSQL+SQLite stack*.

---

## Decisions locked in

| Decision | Choice | Rationale |
|---|---|---|
| Language / web | **Python 3.12 + FastAPI** | Matches this repo (≈979 .py files); OpenAPI is free (a TH5 delta). |
| ORM / DB access | **SQLAlchemy 2.0 + Alembic** | One model → both dialects; the `/query` compiler emits Core expressions; Alembic carries dialect-branched DDL. |
| Databases | **PostgreSQL + SQLite** | PG = prod/multi-tenant; SQLite = dev/single-org/small. **Directly fixes the #1 user pain** (Cassandra/JanusGraph/ES ops weight). |
| **Tenancy enforcement** | **App-layer gate is the source of truth on both engines; Postgres RLS is optional defense-in-depth** | SQLite has **no RLS**. If RLS were primary, SQLite would be unsafe. We lift the PoC's exact predicates into Python (`core/tenancy.py`) as primary, and keep the PoC's SQL policies as a second wall on PG only. |
| API surface | **One clean v1 API only** (skip v0) | The TH4 source carries v0+v1 dual controllers + bespoke renderers; users complain about the v0/v1 split. Drop the legacy baggage. |
| Internal async backbone | **DB-table-as-queue (`SELECT … FOR UPDATE SKIP LOCKED` on PG; single-proc poll on SQLite)** | No broker needed for v1. Kafka/Redis are *notifier sinks*, not the internal bus. |
| Deploy shape | **Monolith app + separable worker processes** (dispatcher, scheduler, Cortex/MISP pollers, function workers) off one codebase/DB | Tenancy correctness depends on one gate + one audit/outbox path; microservices multiply where that must be re-proven. |
| Dropped (matches existing decisions) | WebDAV/TheHiveFS, HDFS blob provider, exact ScalliGraph query grammar, v0 wire compat | Optional/legacy; not load-bearing for the product shape. |
| Dependencies | **All pip installs from the internal Nexus PyPI proxy**, never pypi.org | Org policy. **M0 must verify every dependency is mirrored; if one is missing, STOP and notify — no public fallback.** |

---

## 1. What users actually want (mined from GitHub issues)

Signal = 👍 reactions + comment volume + recurrence, across `TheHive-Project/TheHive` (834 open
issues, archived 2025-07-25), `Cortex`, `Cortex-Analyzers`, and the Python SDKs. The dominant
finding: **operational simplicity outranks any single feature.**

**Highest-signal asks (treat as v1.0-relevant):**

| Want | Signal | In our plan? |
|---|---|---|
| **Case timeline / audit-trail tab** (filterable, case-wide) | #84 — 13 👍 (highest), open 2018→2024 | Yes, but promote from "implement-later" → **must-have** (M6, cheap on audit) |
| **SAML / OAuth SSO** | #768, #2329 — 11 👍, enterprise blocker | Yes (M9) — demand says **pull forward** |
| **Helm chart / Kubernetes-native deploy** | #1224 — 11 👍 | Add (M9 packaging) — net-new |
| **Bulk alert→case merge** | #271 — 11 👍 (shipped in TH4 3.3.0) | Replicate (M6/M7) |
| **Run analyzers automatically on observable/alert creation** | #261 — 10 👍 | Yes = alert pre-processing + trigger (M6/M7) |
| **Export dashboard / case as PDF report** | #558 — 10 👍 | Yes = Case Reporting (M10) |
| **Custom severity / custom fields fully searchable+sortable** | #363, #253, #652, #582 — 6–13 👍 | Yes (M6) — ensure full filter/sort/column lifecycle |
| **Granular RBAC** (e.g. remove *delete* from R/W users; multi-assignee) | #2241, #2259, #2264 — 7 👍 | Refine the permission model (M2) — see gap analysis |
| **SLA / time tracking** | #2431 | = Metrics/KPIs (M6) |
| **Account lockout policy** | #2311 | Yes (M1 auth) |
| **Solid REST API + Pythonic SDK** | TheHive4py #85 rewrite ask | Add a first-party Python SDK deliverable (M3/M8) |
| **STIX 2.1 / better MISP filtering** | #370, #2506 | MISP connector (M7) |
| **JIRA / ticketing integration** | #1537 — 7 👍 | Notifier/connector (M5/M7) |
| **Drag-drop attachments, paste images** | #7 — 9 👍 | Frontend UX (M8) |

**Top pain points a rewrite must *avoid* (these are why people would switch):**
- **Memory/CPU exhaustion & instability** on Cassandra/JanusGraph (Cortex #214 — 47 comments; #1563/#1341 "very slow, permanent high CPU"). → Our PG/SQLite stack is the fix; **make lightweight ops the headline value prop.**
- **Elasticsearch 8 / OpenSearch 2 incompatibility** trapping users on old versions (#2465; Cortex #429 — 47 comments). → We avoid the ES dependency entirely (PG/SQLite full-text; OpenSearch optional).
- **Fragile migrations** losing alerts (#2188, #2238). → Migration tooling needs contract tests.
- **Slow search on large datasets** (#2116, #1959). → Index design + keyset pagination are first-class (M3).
- **API v0/v1 confusion.** → Ship one clean v1 only.

**NFR targets derived from demand:** sub-1s common UI actions; sub-5s search on ~50k cases;
runs comfortably on modest hardware (the opposite of the Cassandra experience); single-binary-ish
dev install (SQLite + uvicorn) and a production Helm chart.

---

## 2. Gap analysis — what the TH4 source has that our docs under-specified

Verified by reading the archived source (`TheHive`, `ScalliGraph`, `Cortex`). These are concrete
items to fold into the build (most are small, but each is a silent-bug source if missed):

| Gap (in source, thin/absent in our docs) | Where it bites | Resolution in this plan |
|---|---|---|
| **Computed properties** — `Case.computed.handlingDuration*`, `Alert.computed.handlingDuration*`, `Alert.imported` | Filtering/sorting/aggregation must support derived fields | Field registry supports `resolver=` (computed) fields, not just columns (B.2) |
| **The 24 `IntegrityCheck` repair jobs** (Data/Case/Tag/Profile/Role/Observable/…) | Data hygiene; denorm reconciliation | First-class `background/integrity/` subsystem; denorm-reconcile job is **load-bearing** for the tenancy fast-path (M4→M9) |
| **Case MERGE semantics** (which case wins, how tasks/observables/alerts/shares consolidate, number handling) | Under-specified everywhere | Flag for explicit design in M6; not just "merge cases" one-liner |
| **4.0.5 freetags migration** — per-org `_freetags_<orgId>` taxonomy | Tag model + queries | Tag/taxonomy model carries per-org freetags taxonomy from the start (M6) |
| **Dual filter strategy** — direct-column vs computed/traversal (subquery/join) | `/query` compiler correctness + perf | Compiler distinguishes the two; tenancy gate re-applied inside traversal subqueries (B.2 risk) |
| **Retry-on-conflict tx wrapper** (`tryTransaction` + backoff; idempotency assumption) | Concurrency on both engines | `core/unit_of_work.py` retry wrapper; side effects only after commit |
| **Cardinality coercion** (single/option/list/set) + enum-as-string | Multi-value columns differ PG↔SQLite | `db/types.py::ArrayOrJson`; enum registry |
| **Renderer recursion / no cycle detection** | Serialization infinite-loops on cyclic graphs | Depth-limited / visited-set serialization |
| **`limitedCountThreshold`** (stop counting past N, return `N+`) | Avoids full scans on huge lists | Built into pagination (B.2) |
| **Actor-based `CaseNumber`** allocator | Monotonic numbers under concurrency | `core/ids.py` → PG sequence / SQLite locked counter (D) |
| **Observable `Data` dedup vertex + `ignoreSimilarity` + `sighted`** | Observable dedup + similar-cases feature | Modeled explicitly (M1 entity, M6 similarity) |
| **Attachment dedup by ref-count** (delete blob only on last reference) | Storage correctness | Attachment store tracks `useCount` (M1/M9) |
| **`describe` cache**, runtime **`Config`** store, **`ReportTag`**, **`HealthStatus`** entities | Misc surfaces | Included in entity set / endpoints |
| **TLP/PAP stored as ints 0–4**, enforced before external connector calls | Cortex/MISP guardrails | Enforced in connector layer (M7) |
| **v0 API + bespoke renderers + WebDAV** | Legacy surface | **Intentionally dropped** (decision above) |

Framework-level traps from ScalliGraph (already reflected in the architecture): `_contains` means
**field/path exists, not substring**; predicate→SQL translation must be per-dialect; full-text
availability changes which operators can be pushed down; pagination is stateless (use keyset).

---

## 3. Must-have feature list (v1.0 blockers) vs nice-to-have

**MUST-HAVE (table-stakes — an IR platform is not credible without these):**

1. **Multi-tenant orgs + sharing + airtight isolation** (the whole reason this is hard) — M2.
2. **Cases / Tasks / Task-Logs / Observables / Alerts** CRUD + lifecycle — M1.
3. **RBAC**: profiles/permissions, the two-level `.can()` gate, **granular enough to remove
   `delete` from read/write users** (#2241) — M1/M2.
4. **The generic `/query` engine + field registry + `/describe`** (the UI runs on it) — M3.
5. **Audit trail + live updates (SSE)** — M4; **Case Timeline tab** (#84) — M6.
6. **Notifications** (email/webhook/Slack/Teams/HTTP) with triggers — M5.
7. **Custom fields (fully searchable/sortable/columns), custom severities/statuses, templates,
   tags/taxonomies, MITRE ATT&CK** — M6.
8. **Cortex** (analyzer/responder enrichment, incl. **auto-run on creation** #261) **+ MISP**
   (import/export, STIX-aware) — M7.
9. **Auth**: local + API key + **lockout** (M1); **SAML + OAuth/OIDC + LDAP** — M9 (**demand says
   pull SAML/OIDC forward** if enterprise adoption is a near-term goal).
10. **First-party REST + Python SDK + OpenAPI** — M3/M8.
11. **Lightweight, scriptable deployment** (SQLite single-org dev; Postgres + **Helm chart** prod)
    — M9. *This is the differentiator, not a footnote.*
12. **Reliable migration/backup tooling** (don't lose data like #2188) — M9.

**NICE-TO-HAVE (sequence after must-haves):** Functions automation engine (M10 — the flagship TH5
delta, high value but not MVP), Case Reporting/PDF (M10), external portal (M10), Kafka/Redis
notifier sinks (M5), advanced dashboards/drag-drop builder (M6/M8), similar-cases ML, JIRA deep
integration (M7), multi-assignee (M6), SLA automation beyond Metrics (M6).

**Explicitly out of scope:** commercial licensing/tiering, Cassandra/ES ops tooling, byte-for-byte
TH5 UI, v0 API compatibility.

---

## 4. Target architecture (FastAPI + SQLAlchemy 2.0 + PG/SQLite)

### 4.1 Module layout (`src/thehive/`)

```
main.py                  app factory, lifespan, router mount, OpenAPI
settings.py              pydantic-settings; engine flag (pg|sqlite), URLs, secrets refs
api/
  deps.py                DI: get_session, get_auth_context, require_perm(...)
  errors.py              RFC7807 + error-accumulating validation
  routers/               thin controllers: case/task/log/observable/alert/share,
                         query (POST /query + GET /describe), stream (SSE),
                         auth/user/org/profile/admin, cortex/misp/function/report
core/
  auth_context.py        AuthContext{user_id, org_id, permissions, principal_kind}
  permissions.py         22 perms + scopes + built-in profiles (+ finer delete perms)
  tenancy.py             *** scope_visible() + require_can() — the gate (4.3) ***
  session_scope.py       per-tx GUC binding (PG) / no-op (sqlite)
  unit_of_work.py        retry-on-conflict tx wrapper + after-commit hook
  ids.py                 CaseNumber allocator (sequence | locked counter)
db/
  base.py types.py engine.py fulltext.py  models/   (dialect-portable types here)
query/
  registry.py operators.py compiler.py aggregations.py pagination.py pipeline.py
services/                one per aggregate; ALL writes go through here (audit+outbox+gate)
repositories/            gate-aware query builders (only entry point for scoped SELECTs)
audit/                   audit_service / outbox / dispatcher / stream(SSE)
notifications/           triggers + notifiers/* + engine
connectors/              cortex/* misp/* runner.py (process|docker|k8s)
functions/               TH5 automation engine (M10): dispatcher/scheduler/worker/sdk_token/egress
attachments/             store interface + local_fs/s3/db + hash dedup (ref-counted)
background/              integrity/* (the 24 checks) + scheduler (leader-elected)
bootstrap/               initial admin, built-in profiles, observable types, seed
workers/                 dispatcher_main / scheduler_main / *_poller_main / function_worker_main
migrations/              Alembic; dialect-branched ops (RLS/arrays/FTS)
tests/                   unit / integration / tenancy_adversarial / query_golden / connectors_contract
```

### 4.2 Request lifecycle (maps to TheHive's `Entrypoint` pipeline)

`HTTP → router (thin) → Depends(get_auth_context)` [auth-provider chain + org resolution] `→
Depends(get_session)` opens UnitOfWork (on PG: `SET LOCAL app.current_org_id/user_id`) `→` Pydantic
validates body (error-accumulating) `→ require_perm(perm)` (Level-1 membership gate) `→ service`
(repository queries via `scope_visible`; writes preceded by `require_can`; `audit_service.emit`
holds the main-action audit; outbox rows staged in the same UoW) `→ commit` (flush final audit
`main_action=true`; register after-commit callback → dispatcher) `→` response model / streamed JSON
+ `X-Total`. On exception → rollback, **nothing dispatched**.

### 4.3 The three hard parts

**(A) Dual-engine tenancy + permission gate — #1 correctness risk.**
- App layer is source of truth on both engines. Three pieces in `core/tenancy.py`:
  `AuthContext` (membership permission set = Level-1); `scope_visible(stmt, entity, ctx)` = `.visible`
  port (transcribes PoC `V3`: case visible iff owned-by-org **or** in a visible `case_share`; tasks/
  observables inherit; alert by org); `require_can(ctx, case_id, perm)` = two-level `.can()` port
  (transcribes PoC `V5`: `perm ∈ ctx.permissions` **AND** the case's share-to-org profile holds `perm`).
- **`RepositoryBase.select(entity)` is the only way to query a scoped table** and auto-applies the
  predicate; a **gate-coverage CI test** fails the build if any router/service issues a bare
  `select(Model)` on a scoped entity. (On SQLite this is the *only* wall.)
- **`organisation_ids` denorm** as a read fast-path (`BIGINT[]`+GIN on PG; JSON+`json_each` on SQLite,
  via `db/types.py::ArrayOrJson`); maintained transactionally by `share_service`; an IntegrityCheck
  reconciles it; tests assert it always equals the `case_share`-derived set.
- **PG RLS (optional 2nd wall):** Alembic emits PoC `V3/V4/V5` policies only on PostgreSQL; app
  connects as non-superuser `thehive_app` (PoC `V4` — superusers bypass RLS) with per-tx GUCs.
- **Connectors/Functions** get a `principal_kind != user` AuthContext → identical gate, no bypass.

**(B) Field registry + `/query` compiler — the linchpin (largest single component).**
- Per-entity `FieldRegistry` of `FieldDescriptor`s: json name; storage mapping (column **or**
  computed/traversal resolver); filterable/sortable/aggregatable; updatable mode
  (`readonly`/`updatable`/`custom`→service callable, e.g. `assignee`, `tags`, `customFields.<name>`).
- **Operator compiler** → SQLAlchemy: `_is/_ne/_lt/_lte/_gt/_gte/_between/_in/_id/_any`,
  `_like`/`_startsWith`/`_endsWith`/`_wildcard`, `_and/_or/_not`, and **`_contains` = exists**
  (NOT substring — the documented trap). Direct-column → `WHERE`; computed → correlated subquery/join
  (with the gate re-applied).
- **Pipeline** with explicit per-step input/output **type tags** (replaces Scala reflection):
  initial steps (`listCase`→Cases, embed the gate), traversal steps (`observables`,`tasks`,`shares`,
  `linkedCases`), generic steps (`filter`,`sort`,`page`,`aggregation`,`count`,`limitedCount`);
  chain validated at parse time.
- **Aggregations** (count/sum/avg/min/max, multi-group-by, **time-histogram** s/m/h/d/w/M/y),
  **keyset pagination** + `X-Total` + `limitedCountThreshold`, **`/describe`** (registry serialized),
  and **OpenAPI as a 2nd renderer over the same registry**.
- **Full-text** behind `db/fulltext.py::FullTextBackend`: PG `tsvector`+`pg_trgm` / SQLite `FTS5` /
  optional OpenSearch — **identical operator semantics across all three.**

**(C) Audit + outbox + after-commit dispatch + SSE.**
- **Deferred main-action audit:** `emit()` holds the pending audit per UoW, flushes the previous as
  secondary, marks the last `main_action=true` at commit; polymorphic `(object)`+`(context)` refs.
- **Outbox in the same tx:** `audit_outbox(UNIQUE(audit_id, topic))` rows for `stream`/`notification`/
  `connector` staged in the same UoW → atomic with the write; **nothing on rollback.**
- **After-commit dispatcher** (separate worker): drains outbox (`SKIP LOCKED` on PG / single-proc on
  SQLite), fans out, marks `delivered_at`, retries with backoff (at-least-once + idempotent consumers).
- **SSE live stream** (replaces long-poll actors): each subscriber holds its AuthContext and
  **re-checks visibility per audit id (2nd gate)** before emitting; coalesced over a grace window;
  multi-node via Redis pub/sub or PG `LISTEN/NOTIFY` (M9). **Activity flow** = `/query` over
  `main_action=true` audits.

### 4.4 Load-bearing libraries (all from internal proxy)
FastAPI, **SQLAlchemy 2.0** (the gate/registry/compiler + dual-dialect ride on it — most load-bearing),
Alembic, Pydantic v2 + pydantic-settings, asyncpg/psycopg + aiosqlite, sse-starlette, Authlib,
python3-saml/pysaml2, ldap3, argon2-cffi/passlib, pyotp, croniter, httpx, Jinja2, boto3, structlog,
prometheus-client, uvicorn+gunicorn, pytest+pytest-asyncio+testcontainers, confluent-kafka/redis,
opensearch-py (optional). **M0 verifies each is mirrored on the Nexus proxy.**

---

## 5. PostgreSQL ↔ SQLite divergence matrix

Isolate every divergence behind a small abstraction; ~95% of code stays dialect-agnostic.

| Concern | PostgreSQL | SQLite | Abstraction |
|---|---|---|---|
| Tenancy primary gate | App-layer (4.3A) | Same code | `core/tenancy.py` (source of truth on both) |
| RLS (2nd wall) | PoC V3/V4/V5 policies + non-superuser role + GUCs | **none** | Alembic `if dialect=='postgresql'` |
| `organisation_ids` denorm | `BIGINT[]`+GIN, `org = ANY(arr)` | JSON text, `EXISTS json_each` | `db/types.py::ArrayOrJson` |
| Other arrays / JSON | `TEXT[]`/`JSONB` (+GIN, `@>`, `->>`) | JSON text (`json_extract`) | `ArrayOrJson` / `JsonType` |
| Full-text / `_like` | `tsvector`+GIN, `pg_trgm` | `FTS5` | `db/fulltext.py` (+optional OpenSearch) |
| Time-histogram | `date_trunc` | `strftime` | `query/aggregations.py` dispatch |
| Case number | `BIGINT IDENTITY` + sequence `nextval` | `AUTOINCREMENT` + locked counter | `core/ids.py` |
| Concurrency / queue claim | MVCC; `FOR UPDATE SKIP LOCKED`; advisory locks | single-writer; `BEGIN IMMEDIATE`+WAL+busy-timeout; single-proc poll | `unit_of_work.py` + `background/scheduler.py` |
| Upsert (dedup) | `ON CONFLICT` | `ON CONFLICT` (3.24+) | SQLAlchemy dialect insert |
| HA stream bus | Redis pub/sub or `LISTEN/NOTIFY` | in-proc (single node) | `audit/stream.py` pluggable |
| Migrations | full DDL incl. RLS/GIN/tsvector | DDL minus PG-only; FTS5 vtables | Alembic branches + **dual-engine migration test** |

**Rule:** SQLite is a first-class dev/single-org engine, not a mock. CI runs the full suite
(especially tenancy-adversarial + `/query` golden fixtures) on **both** engines.

---

## 6. Detailed implementation plan (milestones)

Serial spine **M0→M1→M2→M3→M4**; then **M5/M6/M7/M8/M10** fan out; **M9** starts early, finishes last.
Each milestone names its first **vertical slice** (auth→API→service→repo→DB→gate→audit) — the shape
everything else copies.

- **M0 — Decisions & safety spikes** (~3–5 wk). Engine-portability ADR (app-gate-primary); `/query`
  grammar + **golden fixtures** (dual-engine); audit/outbox atomicity spike; `ArrayOrJson`/`JsonType`
  parity spike; CaseNumber allocator spike; **verify all deps on the Nexus proxy**. *Slice:* one row
  with an array + JSON column reads identically on PG and SQLite.

- **M1 — MVP single-org spine** (~2–3 mo). Alembic baseline (org/user/profile/permission/membership/
  case/task/log/observable/alert/case_share owner-share/tag/audit/audit_outbox; `_created/_updated`
  mixin); auth (session + local pw **+ lockout** + API key)→AuthContext; built-in profiles seed; CRUD
  services+routers; owner-share per case; thin field registry + list/filter/sort; local-FS attachments
  (ref-counted dedup); CaseNumber allocator. *Slice:* **Case end-to-end** on both engines.

- **M2 — Multi-tenancy / RBAC** (~2 mo, **highest risk**). Full org/profile/permission/membership
  (incl. admin scope + **finer delete perms** for #2241); **Share model + lifecycle** (share/unshare
  case/all-tasks/all-observables/single, `actionRequired`, update-profile, **orphan cleanup**);
  org-to-org links; wire `scope_visible`/`require_can` into every M1 service; `organisation_ids` denorm
  + reconcile IntegrityCheck; **PG RLS** policies + `thehive_app` role + GUCs. *Slice:* replay the PoC
  scenario through the real API on both engines; **gate-coverage CI test** active.

- **M3 — Query platform** (~2.5–3.5 mo). Full `FieldRegistry` per entity (incl. **computed** fields);
  generic `POST /query` with type-chaining; complete operator set (incl. `_contains`=exists);
  aggregations; `GET /describe`; keyset pagination + `X-Total` + limited-count; full-text backend;
  **OpenAPI** from the registry; **first-party Python SDK** generated from OpenAPI. *Slice:*
  `[listCase, filter(...), sort(-startDate), page(total)]` identical on both engines, gate-enforced.

- **M4 — Audit / stream / background** (~2 mo). Deferred main-action batching; durable outbox dispatcher
  (retry/idempotency); activity flow; **SSE live stream + 2nd visibility gate**; first IntegrityCheck
  jobs (denorm reconcile, case-number, orphans) on a path to all 24. *Slice:* create case → atomic
  audit+outbox → dispatcher fan-out → owning-org SSE receives, other org receives nothing; rollback
  dispatches nothing.

- **M5 — Notifications** (~1–1.5 mo). Trigger engine (CaseCreated/AlertCreated/TaskAssigned/JobFinished/
  LogInMyTask/CaseShared/AnyEvent/FilteredEvent) on the outbox; notifiers Emailer/Webhook/Mattermost/
  AppendToFile + **Slack/MS Teams/HTTP** + **Kafka/Redis** sinks; per-user/org config + Jinja templating.

- **M6 — Feature breadth + TH5 UX** (~3.5 mo, parallel). Custom fields (typed, mandatory, ordering,
  **fully filterable/sortable/columns**); custom severities/**statuses**; case/task templates; tags +
  taxonomies (per-org freetags; MISP import); MITRE ATT&CK + procedures; dashboards (private/shared);
  KB pages; observable types; TLP/PAP everywhere; **Comments** (#cases/alerts); **Case Timeline tab**
  (#84 — must-have); **Metrics/KPIs** (SLA #2431); **alert pre-processing + observable dedup +
  similar-cases/alerts**; **case MERGE** (explicit design); **bulk ops** (#271). *Slice:* define a
  custom field → set via registry `custom` path → filter via `/query` (proves the registry generalizes).

- **M7 — Connectors** (~2.5–3 mo, parallel). **Cortex** (multi-server client, analyzer jobs incl.
  **auto-run-on-creation** #261, responder actions, **ActionOperations** applier through normal
  services, **TLP/PAP gating**, report templates, polling); **MISP** (scheduled pull, event→alert with
  `(type,source,sourceRef,org)` idempotency + `lastSyncDate`, attribute↔observable map, export +
  write-back, **STIX 2.1**, taxonomy sync); shared `runner.py` (process/docker/k8s);
  **connector-mutation contract tests**. *Slice:* a Cortex `AddTagToCase` op applied **as a service
  principal** through `case_service` (same gate+audit+outbox as a user).

- **M8 — Frontend** (~4–5 mo, parallel from end of M3). SPA (case/alert/observable/task + SSE live
  refresh; **drag-drop attachments** #7), dashboards/charts, admin, **search builder driven by
  `/query`+`/describe`+OpenAPI**, Cortex/MISP UX, upload, MFA enrollment. *Slice:* case list rendered
  entirely from `/query`+`/describe` with live SSE updates. *(Frontend framework: TBD — propose a
  TypeScript SPA; not specified by user.)*

- **M9 — Hardening / ops** (~2.5–3 mo). **SAML + OAuth/OIDC + LDAP/AD + PKI + header SSO** (demand
  says consider pulling SAML/OIDC earlier); session management (list/revoke); self-service password
  reset; **LDAP/AD directory sync**; MFA/TOTP; **HA** (API replicas + Redis/`LISTEN-NOTIFY` bus +
  leader-elected scheduler); S3 + DB attachment providers; health/metrics; **packaging: docker + Helm
  chart** (#1224) + deb/rpm; **backup/restore + migration tooling** (avoid #2188 data loss). *Slice:*
  SAML login → session row → `/user/sessions` lists → `DELETE` revokes.

- **M10 — Functions + reporting** (~2.5–3.5 mo, parallel — the TH5 centerpiece). **Functions** runtime
  (`function`/`function_trigger`/`function_run`, org-scoped/RLS; triggers scheduled[croniter+leader]/
  event[outbox+`FilteredEvent`+`dedup_key`]/manual/API; dispatcher claims runs + mints **short-lived
  scoped token**; **objects SDK = thin scoped REST client** so writes inherit gate+audit+outbox;
  **security:** scoped principal (never user's full rights, no admin scope), **egress proxy +
  allowlist blocking RFC1918/169.254.169.254** (SSRF), CPU/mem/wall-clock/API caps, vault secrets,
  full audit tagged with `function_run.id`, versioning + optional 4-eyes; new perm `manageFunction`).
  **Case Reporting / PDF** (#558 — `report_template` widgets resolved via `/query` so RLS applies →
  Markdown → printable HTML; perm `manageCaseReportTemplate`). **External portal** (constrained external
  share class + External-Reader/Actor profiles). *Slice:* event-triggered Function that on
  `observable.flaggedIoc` calls `ctx.case.addTag(...)` via the scoped-token SDK; shows in the activity
  flow as "Function X changed this"; egress to internal IPs blocked.

---

## 7. Feature implementation plan (per-feature, on this stack)

| Feature | Data model (Postgres/SQLite) | API | Mechanism | M |
|---|---|---|---|---|
| **Org / sharing / tenancy** | `organisation`, `membership`, `case_share(owner,profile)`, `share_task(action_required)`, `share_observable`; `organisation_ids` denorm | gated implicitly on every endpoint | `scope_visible`+`require_can` (4.3A); RLS 2nd wall on PG | M2 |
| **Case/Task/Log/Observable/Alert** | one table each + `_created/_updated`; `observable_data` dedup; `case.number` via allocator | REST CRUD + `/query` | services → audit+outbox; owner-share on create | M1 |
| **Permissions / profiles** | `profile`, `profile_permission` (22 perms + finer delete) | `/profile`, `/permission` | Level-1 in AuthContext; admin-scope only in admin org | M1/M2 |
| **`/query` + `/describe`** | n/a (derives from registry) | `POST /query`, `GET /describe` | registry→operator compiler→SQLAlchemy; keyset paging | M3 |
| **Audit + Timeline** | `audit` (poly object/context, `main_action`), `audit_outbox` | activity flow via `/query`; `timeline` step on case | deferred batching; **Timeline = read-side projection** over audit+entity dates (#84) | M4/M6 |
| **Live stream** | n/a | `GET /stream` (SSE) | after-commit dispatch + per-subscriber 2nd gate | M4 |
| **Notifications** | `notification_config` (user/org), trigger+notifier configs | `/notification/*` | triggers subscribe outbox; notifiers via httpx/SMTP/Jinja | M5 |
| **Custom fields / statuses / severities** | `custom_field` + typed `custom_field_value`; per-org `resolution_status`/`impact_status`/`custom_status` | `/customField`, PATCH entity | registry **dynamic** `customFields.<name>` fields; mandatory/order (#253/#363/#652) | M6 |
| **Templates / tags / taxonomies / MITRE** | `case_template`+tasks/CFs; `tag`,`taxonomy` (per-org freetags); `pattern`(self-hier)+`procedure` | `/caseTemplate`,`/tag`,`/taxonomy`,`/pattern` | taxonomy import; MITRE hierarchy + procedures on case | M6 |
| **Comments** | `comment(subject_type,subject_id,body_md,...)` | `/{case|alert}/{id}/comment` | gated via subject visibility; transfer on alert→case promote | M6 |
| **Metrics / KPIs (SLA)** | `metric`, `case_metric(value)` | PATCH case; read via `/query` agg | numeric per-template, distinct from custom fields (#2431) | M6 |
| **Alert pre-processing / dedup / similar** | reuse `observable_data` dedup; alert observables | `/alert/{id}/analyze`, `/alert/{id}/similar` | run analyzers on alert (#261); upsert `(dataType,data)`; similarity by shared observables | M6/M7 |
| **Bulk ops / merge** | n/a | `/alert/bulkMerge`, `/case/merge` | batch through services; **explicit merge semantics** (gap) | M6 |
| **Cortex** | `job`,`action`,`analyzer_template`,`report_tag` | `/cortex/*` | poller; ActionOperations applier through services; TLP/PAP gate; **auto-run-on-create** | M7 |
| **MISP** | per-server config; alert idempotency key | `/misp/*` | scheduled import/export; attr↔obs map; STIX 2.1; write-back | M7 |
| **Auth providers** | `session`, provider configs | `/auth/*`, `/user/sessions` | provider chain; **SAML/OIDC/LDAP** (pull SAML fwd); lockout; reset; dir-sync | M1/M9 |
| **Attachments** | `attachment(hashes,use_count)` + blob store | upload/download | provider iface (local/S3/DB); ref-counted hash dedup | M1/M9 |
| **Functions (automation)** | `function`,`function_trigger`,`function_run(dedup_key)` | `/function/*` | scoped-token SDK; 4 trigger modes; **SSRF egress allowlist**; full audit (M10 detail above) | M10 |
| **Case Reporting / PDF** | `report_template(definition jsonb)` | `GET /case/{id}/report` | widgets resolved via `/query` (RLS applies) → MD → printable HTML (#558) | M10 |
| **Integrity / ops** | n/a | admin endpoints | 24 IntegrityCheck jobs; backup/restore; Helm; migration tooling | M4/M9 |
| **Python SDK** | n/a | generated client | from OpenAPI; the ecosystem ask (TheHive4py #85) | M3/M8 |

---

## 8. Top risks (this stack)

1. **Dual-engine tenancy without RLS on SQLite (highest).** One bare `select(Case)` = cross-tenant
   leak on SQLite. Mitigate: gate-only repository entry point + gate-coverage CI test + PG RLS backstop.
2. **`/query` compiler + registry (largest build).** Reflection→explicit type tags; **identical
   operator/aggregation/full-text semantics PG↔SQLite**; the `_contains`=exists and `_like`/date-bucket
   traps. Mitigate: golden fixtures on both engines from M0.
3. **Commit-coupled fan-out + SSE 2nd gate.** Never publish inline; no phantom events on rollback.
   Mitigate: `emit` only stages; dispatcher is the sole publisher; rollback + 2nd-gate tests.
4. **CaseNumber + concurrency divergence** (`nextval`/`SKIP LOCKED` vs locked-counter/single-writer)
   + retry-on-conflict idempotency. Concentrated in `core/ids.py`, `unit_of_work.py`, `dispatcher.py`.
5. **Functions security (M10).** Scriptable + outbound = privilege-escalation + SSRF. Non-negotiable:
   scoped short-lived token, SDK-as-REST-client (no second enforcement path), egress allowlist
   (blocks internal/metadata IPs), resource caps, full audit.
6. **Connector mutation contract (M7).** Every connector write must produce the same gate/audit/outbox
   evidence as a user write; MISP idempotency; TLP/PAP before external calls. Mitigate: contract tests.
7. **Async vs sync workers.** Async API + possibly **sync** workers (dispatcher/scheduler/pollers);
   keep models/registry dialect- and sync/async-agnostic.
8. **Dependency availability on the Nexus proxy.** Pre-check at M0 (esp. python3-saml, confluent-kafka,
   opensearch-py); a missing package → STOP and notify (no pypi.org fallback).

---

## 9. Critical files / references

Design substrate (this repo — the build transcribes these, it doesn't replace them):
- [`thehive-api-internals.md`](./thehive-api-internals.md) — request lifecycle, `/query`+PublicProperties, audit→commit→stream
  (drives §4.2, §4.3B, §4.3C).
- [`poc-postgres/migrations/V3__row_level_security.sql`](./poc-postgres/migrations/V3__row_level_security.sql) + [`V5__permissions.sql`](./poc-postgres/migrations/V5__permissions.sql) — the verified
  `.visible` and two-level `.can()` predicates to transcribe into `core/tenancy.py` and the PG RLS layer.
- [`thehive4-parity-spec.md`](./thehive4-parity-spec.md) — relational DDL (incl. `audit`/`audit_outbox`), operator set,
  engine-divergence notes (seed §5).
- [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md) — exact share/unshare lifecycle + orphan-cleanup (M2).
- [`thehive5-features-design.md`](./thehive5-features-design.md) + [`cortex.md`](./cortex.md) — Functions engine + scoped-principal/SSRF (M10),
  Cortex ActionOperations/TLP-PAP contract (M7).
- [`thehive4-roadmap.md`](./thehive4-roadmap.md) — milestone shape that §6 refines for this stack.

New code lives under a new top-level package (proposed `src/thehive/`), separate from the existing
monitoring/CDK/Terraform code in this repo.

---

## 10. Verification (how we prove it end-to-end)

- **Dual-engine CI matrix:** every suite runs on **PostgreSQL (testcontainers) and SQLite**.
- **Tenancy adversarial suite** (`tests/tenancy_adversarial/`): replays the PoC scenario through the
  real API; asserts org-isolation + two-level write gate on both engines; **gate-coverage** test fails
  on any un-gated scoped query; property test: app-gate set == `case_share`-derived == (PG) RLS == denorm.
- **`/query` golden fixtures** (`tests/query_golden/`): request→expected corpus on both engines; unit
  tests per operator; explicit `_contains`=exists test; keyset-pagination stability.
- **Audit/stream tests:** rollback ⇒ 0 outbox rows + 0 dispatches; commit ⇒ exactly one `main_action`
  audit + expected outbox; SSE 2nd-gate (other org receives nothing); redelivery fires notifier once;
  one request creating case+3 tasks ⇒ one `main_action` audit + N secondaries.
- **Connector contract tests:** every Cortex/MISP write produces the same gate/audit/outbox evidence as
  a user write; MISP idempotency on re-import; TLP/PAP rejection before external calls.
- **Functions security tests (M10):** scoped token cannot exceed its profile; egress to RFC1918/
  169.254.169.254 blocked; resource caps enforced; every function write audited with `function_run.id`.
- **Migration tests:** Alembic up/down on both engines; a TH4-import fixture that asserts no data loss
  (the #2188 regression).
- **Run it:** `uvicorn` + SQLite for a zero-dependency dev boot; `docker compose` (PG) for integration;
  Helm chart smoke-deploy for M9. Manual acceptance: create→enrich(Cortex)→share→close a case via API
  and confirm audit/timeline/notifications.
