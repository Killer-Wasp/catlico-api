# TheHive / Cortex — Architecture & Rewrite Docs

Reverse-engineered reference documentation for the
[TheHive Project](https://github.com/TheHive-Project) stack (TheHive 4/5 +
Cortex), produced from a read of the actual source, plus a design for
rewriting the TheHive 4 product shape on a simpler PostgreSQL/OpenSearch stack.

The rewrite target is **not byte-for-byte compatibility** with TheHive 4.
The docs separate source-accurate mechanisms from compatibility choices:
tenant isolation, audit consistency, case/task/observable semantics, and
Cortex/MISP mutation paths are core; v0 API compatibility, WebDAV/TheHiveFS,
HDFS/blob-provider parity, and exact ScalliGraph query syntax are optional.

> **Context:** TheHive is a graph database (JanusGraph via the in-house
> *ScalliGraph* framework); Cortex is a stateless Scala/Play API over
> Elasticsearch that runs pluggable analyzers/responders. None of this is a
> relational SQL schema out of the box.

## The docs

| Doc | What's in it |
|---|---|
| [`thehive-data-model.md`](./thehive-data-model.md) | TheHive's **graph schema** — all 30 vertices + ~45 edges, full + per-domain Mermaid ER diagrams, vertex/edge reference tables. |
| [`thehive-api-internals.md`](./thehive-api-internals.md) | **How the API works**: the `Entrypoint` pipeline, auth chain, `FieldsParser`, the `PublicProperties` registry, the reflection-typed `/query` DSL, and the **write path** (deferred audit batching → publish-on-commit → live stream + notifications). Two sequence diagrams. |
| [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md) | **Operation-level mechanisms** for roles/membership and case/task/observable **sharing**: exact entities/edges/denormalization/audit/permission per op, with the PostgreSQL equivalent and the orphan-cleanup subtlety. |
| [`cortex.md`](./cortex.md) | **Cortex**: analyzers vs responders, the job-runner mechanism (process/docker/k8s), the cortexutils I/O contract, TLP/PAP guardrails, the Cortex data model, and how it plugs into TheHive. ER + job-run sequence diagram. |
| [`thehive-misp-connector.md`](./thehive-misp-connector.md) | **MISP connector**: scheduled event→alert import, case→event export, the attribute↔observable mapping, sync filtering, and idempotency. Sync sequence diagram. |
| [`thehive4-parity-spec.md`](./thehive4-parity-spec.md) | **Rewrite spec**: feature-parity checklist across 14 subsystems, a component/architecture diagram, and a **PostgreSQL-instead-of-JanusGraph** data model (mapping rules + relational ER + DDL). |
| [`thehive4-roadmap.md`](./thehive4-roadmap.md) | **Phased roadmap**: the parity checklist sequenced into milestones (M0–M10) with T-shirt effort sizing, a critical-path diagram, and where the risk/mass concentrates. |
| [`thehive5-feature-gap.md`](./thehive5-feature-gap.md) | **Competitor gap**: what the commercial **TheHive 5** added beyond OSS TheHive 4 (Functions automation, Timeline, Metrics/KPIs, Comments, Case Reporting, SAML, more notifiers…), with Implement/Defer/Skip calls and roadmap homes. |
| [`thehive5-docs-map.md`](./thehive5-docs-map.md) | **Documentation map & digest** of the official TheHive 5 docs (docs.strangebee.com): full section tree (Installation, Operations, Analyst Corner, Organization config, Administration, API, Release notes) with URLs + per-area summaries. |
| [`thehive5-docs-deep.md`](./thehive5-docs-deep.md) | **Deep digest** expanding each map section: Functions objects/types/methods, all auth providers, dashboard widget types, the full notification trigger/notifier lists, observables, profiles/permissions, Cortex/MISP specifics — plus newly surfaced features (External Portal, Kafka/Redis notifiers, custom statuses). |
| [`OFFLINE.md`](./OFFLINE.md) + [`thehive5-doc-links.txt`](./thehive5-doc-links.txt) | **Offline access**: a 93-URL manifest of the official docs + `wget`/`httrack` commands to mirror them on your own machine (the docs site 403-blocks this sandbox; the digests above are the offline-readable summaries kept here). |
| [`thehive5-features-design.md`](./thehive5-features-design.md) | **Mechanism design** for each TH5-parity feature: data model, API, execution mechanism, security, edge cases. Deep dive on the **Functions** automation engine (sandbox + scoped-token SDK + trigger dispatch). |
| [`poc-postgres/`](./poc-postgres/) | **Runnable PoC**: PostgreSQL schema migrations + Row-Level-Security proof of both the org-scoped Share **visibility** gate and the two-level write-**permission** gate (the `.can()` equivalent). Verified against PG 16. |
| [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) | **The build plan**: how to implement the alternative on **Python 3.12 + FastAPI + SQLAlchemy + PostgreSQL/SQLite**. Source-verified gap analysis, GitHub-issue user-demand mining, must-have feature list, M0–M10 milestones + per-feature plan, the PG↔SQLite divergence matrix, and verification. Built on a source audit of the real TH4 code. |
| [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md) | **Community demand**: what OSS users actually asked for, mined from the archived TheHive/Cortex GitHub issues (ranked themes, top-20 specific asks, pain-points-to-avoid, must-have vs nice-to-have, and a reconciliation to TH5/our roadmap). The *demand* axis to the feature-gap's *supply* axis. |
| [`thehive5-design-improvements.md`](./thehive5-design-improvements.md) | **How we beat TheHive**: proposed design changes that make the alternative better than a clone — the core deltas already adopted (relational stack, app-layer tenancy, durable outbox, OpenAPI-first) plus new proposals (observability, event-sourced timeline, finer RBAC, async bulk jobs, verifiable migration, demo mode…), each with trade-offs + a prioritization. |
| [`thehive4-pitfalls-and-gotchas.md`](./thehive4-pitfalls-and-gotchas.md) | **The landmines**: existing TH4 bugs, source-level traps, and PG/SQLite stings that will bite a reimplementation — multi-tenant leaks, audit/commit consistency, retry idempotency, `_contains`=exists, hash-blob ref-counting, connector races, migration data-loss, the Cassandra/ES ops pain — each with the lesson + a pre-flight checklist. |
| [`newhive-rbac-design.md`](./newhive-rbac-design.md) | **Our product — fine-grained RBAC**: a `verb:resource` permission model (`read:cases`, `write:cases`, `delete:cases`…) replacing TheHive's coarse 22 perms, inside the two-level tenancy gate. Full permission catalog, built-in profiles, enforcement mapping, and a migration map from TheHive's permissions. |
| [`newhive-enrichment-plugins.md`](./newhive-enrichment-plugins.md) | **Our product — pluggable observable enrichment**: a Cortex-like but add/remove **plugin** system (geoip2, crowdstrike, misp…), each plugin checks/enriches an observable. Plugin contract + manifest, lifecycle, sandboxed/secure execution, per-org config, and Cortex compatibility so the existing catalog still works. |
| [`newhive-plugin-catalog.md`](./newhive-plugin-catalog.md) | **Plugin catalog**: every Cortex analyzer (155) & responder (48), enumerated from the source repo, grouped by capability with data types — the menu of plugins to build, plus a free/local-first build-priority plan and the Cortex-bridge strategy for the long tail. |

## Suggested reading order

1. **Understand the system** → `thehive-data-model.md`, then `thehive-api-internals.md`, then `cortex.md`.
2. **Plan a rewrite** → `thehive4-parity-spec.md` (what to build), then `thehive4-roadmap.md` (in what order).
3. **Build it** → `thehive5-implementation-plan.md` (the approved, stack-specific implementation plan: Python/FastAPI/PostgreSQL+SQLite, milestones, must-have features, verification).
4. **Avoid the landmines** → `thehive4-pitfalls-and-gotchas.md` (known TH4 bugs, source traps, and PG/SQLite stings to design around before you start).
5. **Design the product (new-hive specifics)** → `newhive-rbac-design.md` (fine-grained permissions), `newhive-enrichment-plugins.md` + `newhive-plugin-catalog.md` (the plugin system + the full analyzer/responder menu to build).

## The three load-bearing ideas (if you read nothing else)

1. **Multi-tenancy is enforced in the data layer.** A `Case` is visible to an org
   only via a `Share` edge, and that `visible`/`can` filter is baked into every
   query — not applied as an afterthought. (Relational equivalent: Postgres
   Row-Level Security.) This is the #1 correctness risk in any rewrite.
2. **The generic `/query` engine + `PublicProperties` field registry is the
   platform.** Filter, sort, aggregation, update, and `describe` all derive from
   one declarative registry; the whole UI is built on the `/query` endpoint.
3. **Audit/stream consistency comes from the commit hook.** Mutations defer a
   single "main action" audit and fan out to the live stream + notifications
   *only on transaction commit*, so the UI and alerts never see phantom events.

## Rewrite decision guide

| Keep in the core rewrite | Simplify intentionally | Make optional / drop |
|---|---|---|
| Org-scoped Share visibility, per-org memberships, profile permissions, and RLS-backed reads | One clean API instead of v0+v1 wire compatibility | Legacy client compatibility unless explicitly required |
| Case/task/log/observable/alert services, case numbering, custom fields, tags, templates, dashboards | SSE/WebSocket or durable outbox instead of Akka actor/long-poll internals | Exact ScalliGraph reflection/runtime type machinery |
| Audit rows with context/object links, main-action activity flow, publish-after-commit fan-out | PostgreSQL joins/CTEs instead of Gremlin traversals | WebDAV/TheHiveFS unless analysts still need mounted-file workflows |
| Cortex/MISP connectors as trusted writers through the same service/audit/tenancy path | Object storage-first blobs instead of every upstream provider | HDFS/database blob-provider parity |

---

*Sources: [`TheHive`](https://github.com/TheHive-Project/TheHive),
[`ScalliGraph`](https://github.com/TheHive-Project/ScalliGraph),
[`Cortex`](https://github.com/TheHive-Project/Cortex),
[`cortex-analyzers`](https://github.com/TheHive-Project/cortex-analyzers).*
