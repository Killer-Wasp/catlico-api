# Design Improvements — making the alternative *better* than TheHive

> A clean-room rewrite is the chance to fix what TheHive got awkward and add what modern IR teams
> expect — not just re-implement TH4/TH5. Every proposal here is grounded in either the
> **source audit** (see the gap analysis in [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) §2)
> or **community demand** (see [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md)),
> not novelty for its own sake.
>
> Each item: **Baseline** (what TheHive does) → **Change** → **Why better** → **Cost / trade-off**
> → **Status** (✅ already in the plan, or 🔵 new proposal). Section D ranks them.

---

## A. Core deltas already adopted in the plan (the design changes that define this rewrite)

These are the load-bearing improvements already baked into the implementation plan — listed here so
the "how we improve on TheHive" story is complete in one place.

**A1. Relational PostgreSQL/SQLite instead of JanusGraph + Cassandra + Elasticsearch.** ✅
*Baseline:* a graph DB (JanusGraph) over Cassandra + an ES index. *Change:* a relational model on
PG (prod) / SQLite (dev). *Why better:* the graph stack is the **#1 community pain** — OOM, CPU
spikes, ES-version lock-in, hard ops (Cortex #214, #2465). IR data is overwhelmingly relational
(cases→tasks→logs, cases↔observables) and rarely needs arbitrary graph traversal. *Cost:* a few
genuinely graph-ish queries (similar-cases, link analysis) become joins/recursive CTEs; we lose
JanusGraph's built-in full-text (replaced by tsvector/FTS5/optional OpenSearch).

**A2. Centralized app-layer tenancy gate + Postgres RLS as a backstop.** ✅
*Baseline:* visibility (`.visible`/`.can`) baked into every Gremlin traversal — correct but
fragile, since one forgotten step leaks data. *Change:* one `scope_visible`/`require_can` gate that
every repository query must pass, enforced by a **gate-coverage CI test**, with PG RLS as a second
wall. *Why better:* the rule lives in one place and is mechanically verifiable; defense-in-depth on
PG. *Cost:* discipline — the repository base must be the only query entry point.

**A3. Durable outbox + after-commit dispatcher instead of in-VM Akka actors.** ✅
*Baseline:* a JanusGraph transaction listener publishing to per-session `StreamActor`s + a
`NotificationActor`; state is in-memory and dies with the node. *Change:* an `audit_outbox` row
written in the same transaction, drained after commit by a separate worker (`SKIP LOCKED`). *Why
better:* survives restarts, scales horizontally, no lost events, and is trivially testable
(rollback ⇒ nothing dispatched). *Cost:* at-least-once delivery ⇒ consumers must be idempotent.

**A4. One clean v1 API, OpenAPI-first, SDK auto-generated.** ✅
*Baseline:* dual v0+v1 controllers with bespoke renderers; users were confused which to use, and
the Python SDK lagged (TheHive4py #85). *Change:* a single v1 API; FastAPI emits OpenAPI; the
Python SDK is generated from it. *Why better:* one contract drives `/describe`, OpenAPI, the search
builder, and the SDK. *Cost:* no wire-compat for existing TheHive clients (acceptable — we provide a
migration + a new SDK).

**A5. Explicit, typed field registry + type-tagged query pipeline instead of Scala macros/reflection.** ✅
*Baseline:* `PublicProperties` via Scala macros and runtime reflection for query type-checking —
powerful but opaque and Scala-only. *Change:* plain Python `FieldDescriptor`s and explicit
input/output **type tags** per query step. *Why better:* readable, unit-testable, and extensible by
contributors without macro magic. *Cost:* we hand-declare what macros generated — more boilerplate,
caught by tests.

**A6. Secure-by-default automation (Functions).** ✅
*Baseline:* TH5 bolted Functions on later. *Change:* design the scoped short-lived token,
**SDK-as-REST-client** (so function writes inherit the same gate/audit/outbox — no second
enforcement path), **egress allowlist** (block RFC1918/169.254.169.254), and resource caps from
day one. *Why better:* the privilege-escalation/SSRF surface is contained by construction, not
patched later. *Cost:* slightly more upfront design in M10.

---

## B. New proposals to consider (beyond the current plan)

**B1. First-class observability (OpenTelemetry + Prometheus + structured logs).** 🔵
*Baseline:* TheHive had minimal built-in telemetry; debugging perf issues (#2116, #1959) was hard.
*Change:* OTel traces across the request→service→DB→dispatcher path, Prometheus metrics
(request latency, query time, outbox lag, dispatcher backlog, per-org counts), and structured logs
keyed by `request_id`. *Why better:* directly attacks the "why is it slow / stuck" pain; and it's
**on-brand for this repo** (a Prometheus/Grafana monitoring stack) — ship a Grafana dashboard with
the app. *Cost:* small per-request overhead; one more dependency tier (`opentelemetry-*`,
`prometheus-client` — both must be on the Nexus proxy). **Recommend: adopt, fold into M4/M9.**

**B2. Event-sourced audit as the backbone; timeline / activity / notifications as projections.** 🔵
*Baseline:* the audit log is a side-effect of mutations; the activity flow and (TH5) timeline are
separate readers. *Change:* treat the append-only audit/event log as the **source of truth for
"what happened"**, and derive the **Case Timeline** (#84), the activity flow, notifications, and the
live stream as *projections* over that one log. *Why better:* one mechanism yields the highest-demand
feature (timeline) plus activity + notifications for free, with replay/debug ("rebuild the timeline")
and a clean audit story. *Cost:* requires disciplined, well-typed event payloads (not free-text
`details` strings as TH4 used). **Recommend: adopt the projection framing in M4/M6; it's cheaper
than building timeline separately.**

**B3. Finer-grained, condition-aware RBAC.** 🔵
*Baseline:* coarse verbs (`manageCase` bundles create/read/update/delete/assign/close); single
assignee; admins couldn't easily "remove delete from read/write users" (#2241) or assign a case to
several people (#2259). *Change:* split coarse permissions into finer verbs; support **multi-assignee**;
add optional **ABAC conditions** (e.g. a profile that may act only up to a given TLP, or only on cases
it owns). *Why better:* matches enterprise SOAR expectations and three concrete community asks.
*Cost:* a richer permission model + more test cases; keep the simple built-in profiles as presets so
the default UX stays easy. **Recommend: adopt the finer verbs + multi-assignee in M2/M6; treat ABAC
conditions as optional.**

**B4. Async bulk-operation job model with progress.** 🔵
*Baseline:* bulk actions were effectively per-item; large merges/closes risked timeouts and partial
state. *Change:* model bulk merge/close/tag/share as **first-class async jobs** (`bulk_job` rows)
with progress, partial-failure reporting, and resumability — same outbox/worker infra as connectors.
*Why better:* answers the bulk-ops demand (#271 and kin) without long-held HTTP requests, and gives
the UI a progress bar instead of a spinner that may time out. *Cost:* a small job-tracking surface +
UI affordance. **Recommend: adopt in M6, reusing the M4 dispatcher.**

**B5. Migration = idempotent typed import + a reconciliation/verification report.** 🔵
*Baseline:* TH3→TH4 migration silently lost alerts (#2188, #2238); users couldn't tell. *Change:*
import is idempotent (re-runnable), strongly typed per entity, and ends with a **verification pass**
that asserts source/target counts per entity per org and emits a **diff report**; refuse to "succeed"
on mismatch. *Why better:* turns the scariest operation (data migration) into a checkable one — a
direct trust-builder for anyone leaving real TheHive. *Cost:* the verifier is extra work, but it's
the difference between "migrated" and "migrated and proven." **Recommend: adopt in M9; it's a
headline adoption feature.**

**B6. Clean, documented extension points (Python `Protocol`s) for the whole ecosystem.** 🔵
*Baseline:* TheHive's connector/notifier/storage SPI was Scala/Akka-heavy — extending meant writing
Scala. *Change:* define small, documented Python `Protocol`s for **storage providers, full-text
backends, notifiers, auth providers, analyzers/responders, and connectors**, discoverable via entry
points. *Why better:* the community can add an integration (the JIRA #1537 / webhook asks) by
shipping a small Python package — no fork, no Scala. *Cost:* designing stable interfaces early; treat
them as semver'd contracts. **Recommend: adopt incrementally — define each Protocol when its first
implementation lands (M5 notifiers, M7 connectors, M9 storage/auth).**

**B7. Sandboxed runtime + a declarative "no-code" automation tier for Functions.** 🔵
*Baseline:* TH5 Functions = user code in a worker. *Change:* offer two tiers — (a) a **declarative
rule** tier ("when alert matches X, run analyzer Y, set severity Z, notify W") that covers the common
80% with no code and no arbitrary-execution risk, and (b) sandboxed code (container, or **WASM** for
admin-authored snippets) for the rest. *Why better:* most automations are simple; the no-code tier
removes the SSRF/privilege surface entirely for them and is far easier to author/audit. *Cost:* two
execution paths to build/maintain. **Recommend: build the declarative tier first in M10; add the code
tier after.**

**B8. Deployment tiers + seeded demo mode.** 🔵
*Baseline:* getting TheHive running was heavy (Cassandra/ES). *Change:* three documented tiers —
**dev** (`uvicorn` + SQLite, single process, zero external deps), **eval** (Docker Compose: app +
Postgres), **prod** (Helm chart: API replicas + Postgres + Redis + workers) — plus a `--demo` boot
that seeds two orgs, sample cases/alerts, and a Cortex stub. *Why better:* "try it in 60 seconds"
is the strongest counter to the ops pain that drove people away (Helm #1224). *Cost:* maintaining the
compose/Helm assets + a seed dataset. **Recommend: adopt — dev/eval in M1, Helm in M9.**

**B9. Data-model simplifications.** 🔵
*Baseline:* User→Role→Profile→Organisation chain; polymorphic edges; computed values bolted on.
*Change:* collapse membership into one `membership(user, org, profile)` row (already in the PoC);
prefer typed FKs + a discriminator over fully polymorphic edges where the target set is small; make
**computed fields** (handlingDuration, imported) first-class registry citizens rather than special
cases; model TLP/PAP/severity as validated enums. *Why better:* fewer joins, clearer schema, easier
RLS. *Cost:* a couple of places (audit context/object) genuinely need polymorphism — keep it there.
**Recommend: adopt (mostly already implied by the relational model) in M1/M2.**

**B10. Query ergonomics: saved searches + stable cursors + a documented grammar.** 🔵
*Baseline:* the `/query` DSL was powerful but under-documented; `_contains`-means-exists tripped
people; pagination was stateless/offset. *Change:* ship a **documented grammar** with golden
examples, return a **stable keyset cursor** in list responses (not just offsets), and add **saved
searches** (per-user/org named `/query` bodies) surfaced in the UI. *Why better:* fewer foot-guns,
deep pagination that doesn't degrade, and a quality-of-life feature analysts expect. *Cost:* minor
additions on top of M3. **Recommend: adopt with M3.**

---

## C. What NOT to change (load-bearing and correct — keep them)

- **The Share / org-visibility semantics + two-level `.can()`** — this is the hard-won correctness
  core; we re-implement it faithfully (just relocate enforcement to the app layer, A2).
- **Deferred main-action audit + publish-strictly-on-commit** — the mechanism that prevents phantom
  events; keep it exactly (A3 just changes the transport).
- **TLP/PAP guardrails and the Cortex worker I/O contract** — keep the analyzer/responder JSON
  contract compatible so the **existing Cortex-Analyzers catalog still works** (don't strand the
  ecosystem).
- **The `/query` + field-registry model** — it's the right abstraction (the whole UI rides on it);
  we improve the *implementation* (A5, B10), not the concept.
- **Case numbering, custom fields, templates, taxonomies, MITRE** — proven domain model; reproduce.

> Guard against over-engineering: resist adding a message broker, microservices, or a graph DB
> "for flexibility." The relational + outbox design is deliberately boring; that's the point.

---

## D. Prioritization

| Proposal | Impact | Effort | Recommendation |
|---|---|---|---|
| A1–A6 (core deltas) | High | (in plan) | ✅ already adopted |
| B8 Deployment tiers + demo | High (adoption) | Low–Med | **Adopt** — dev/eval M1, Helm M9 |
| B1 Observability | High | Low–Med | **Adopt** — M4/M9 (ship a Grafana dashboard) |
| B5 Verifiable migration | High (trust) | Med | **Adopt** — M9 |
| B2 Event-sourced projections | High | Med | **Adopt framing** — M4/M6 (cheaper timeline) |
| B3 Finer RBAC + multi-assignee | Med–High | Med | **Adopt** verbs+multi-assignee M2/M6; ABAC optional |
| B4 Async bulk-job model | Med–High | Low–Med | **Adopt** — M6 on M4 infra |
| B10 Query ergonomics | Med | Low | **Adopt** — with M3 |
| B6 Extension Protocols | Med (ecosystem) | Med | **Adopt incrementally** — M5/M7/M9 |
| B7 No-code automation tier | Med–High | Med | **Adopt declarative tier first** — M10 |
| B9 Data-model simplifications | Med | Low | **Adopt** — M1/M2 |

**Net effect:** none of B1–B10 sits on the serial spine (M0→M4) except where it *replaces* work
already planned (B2 makes the timeline cheaper; B8 dev/eval is basically M1 packaging). They extend
breadth and adoption, not the critical path — the same shape as the TH5-parity work.

---

## Cross-references

- Demand behind these: [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md)
- Competitor deltas: [`thehive5-feature-gap.md`](./thehive5-feature-gap.md)
- Where each lands: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (milestones)
- Mechanisms being improved: [`thehive-api-internals.md`](./thehive-api-internals.md),
  [`thehive-data-model.md`](./thehive-data-model.md), [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md)
