# TheHive — Community-Requested Features (mined from GitHub issues)

> **What this is:** the *demand* axis — what the open-source TheHive/Cortex community actually
> asked for, mined from the public GitHub issue trackers. It complements
> [`thehive5-feature-gap.md`](./thehive5-feature-gap.md) (the *competitor* axis — what the
> commercial TheHive 5 added beyond OSS TheHive 4) and feeds the must-have list + milestones in
> [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) §1/§3.
>
> **Two different axes:**
> - *Feature gap* = "what the paid product has that the free one didn't." (supply)
> - *Community requests* = "what users kept asking the project to build." (demand)
>
> They overlap (timeline, SAML, custom severities) but each also contains items the other
> doesn't — most importantly, the community's loudest asks are **operational** (deployment,
> performance, stability), which no feature table captures.

## Methodology & sources

Signal = 👍 reactions + comment volume + recurrence across the archived OSS repos:
`TheHive-Project/TheHive` (≈834 open issues at archival, **archived 2025-07-25**), `Cortex`,
`Cortex-Analyzers`, and the Python SDKs (`TheHive4py`, `Cortex4py`). The OSS line stopped when
StrangeBee moved to closed-source TheHive 5, so these issues are a **historical record of
OSS-era demand** — exactly the audience an open alternative would recapture.

> Counts/issue numbers are as-mined from the trackers (point-in-time); treat them as relative
> signal, not exact live figures.

---

## 1. Top wants — themes (ranked by signal)

1. **Case timeline / audit-trail tab** — a structured, filterable, case-wide timeline (task
   created, observable added, status changed). #84 — **13 👍** (highest-reacted in the set), open
   2018→2024 ("much needed"). → TH5 added a timeline; **must-have** in our plan (M6, cheap on the
   audit stream).
2. **Bulk operations & alert→case merge** — merge dozens of phishing/spam alerts into one case in
   one action. #271 — 11 👍, 11 comments (shipped in TH4 3.3.0); related: detach/unmerge #2303,
   #2300. → M6.
3. **SAML / OAuth SSO** — SAML 2.0 for AD/Okta/Azure AD; "OAuth is defacto, SAML is the blocker."
   #768 — 11 👍, #2329 open. → TH5 added SAML 5.1; our plan M9 (**demand says pull forward**).
4. **Custom fields & custom severities** — searchable/filterable/sortable custom fields, custom
   severity levels, mandatory + multi-value. #363 (6 👍,12c, shipped 4.0.0-RC2), #253 (open,13c),
   #652, #582. → M6, ensure full filter/sort/column lifecycle.
5. **Analyzer/responder execution control** — auto-run analyzers on observable/alert creation;
   responder input parameters; run from alert preview. #261 — 10 👍; Cortex #180 — 7 👍; #792,
   #862. → M6 (alert pre-processing) + M7 (Cortex).
6. **Operational simplicity / modern infra** — ES 8 / OpenSearch 2 support, Helm chart, lower
   resource footprint. Cortex #429 (47 comments), #2465, #1224 (11 👍, Helm), Cortex #214 (47
   comments, OOM/CPU). → **This is the headline value prop** (we use PG/SQLite, ES optional).
7. **Dashboards / reporting / export** — PDF export of dashboards & case reports, clickable
   widgets. #558 — 10 👍; #2138, #2456. → M10 (Case Reporting), M6 (dashboards).
8. **Alert lifecycle UX** — triage-notes scratchpad on alert preview; keep alert date on case
   creation; control alert-body auto-append on merge. #1800 (7 👍), #2408, #2303. → M6.
9. **MISP / STIX / threat-intel** — pre-create MISP event filtering, bi-directional event update,
   STIX 2.1 import. #370, #2209, #2506. → M7.
10. **Granular RBAC & multi-tenancy** — remove *delete* from read/write users; assign a case to
    multiple people; root-org cross-org alert view. #2241, #2259, #2264. → M2 (permission-model
    refinement).
11. **API completeness & a Pythonic SDK** — full v1 API coverage; rewrite the Python client.
    TheHive4py #85 (rewrite ask), #157; Cortex4py #14. → M3/M8 (first-party SDK from OpenAPI).

---

## 2. Most-requested specific features

Ranked by signal. **TH5?** = does the commercial TH5 already have it (per `thehive5-feature-gap.md`);
**Plan** = milestone home in `thehive5-implementation-plan.md`.

| # | Feature | Signal | TH5? | Plan |
|---|---|---|---|---|
| 1 | Case timeline / audit-log tab | #84 — 13 👍, 10c | Yes | **M6 (must-have)** |
| 2 | SAML authentication | #768/#2329 — 11 👍 | Yes (5.1) | M9 (pull fwd) |
| 3 | Bulk alert→case merge | #271 — 11 👍, 11c | Yes (TH4 3.3.0) | M6 |
| 4 | Helm chart / K8s-native deploy | #1224 — 11 👍 | Partial (cluster tooling) | **M9 — net-new for us** |
| 5 | Run analyzers on observable/alert creation | #261 — 10 👍 | Yes (alert pre-proc) | M6/M7 |
| 6 | Export dashboard / case as PDF | #558 — 10 👍 | Yes (Reporting, Platinum) | M10 |
| 7 | Custom severity levels | #363 — 6 👍, 12c | Yes | M6 |
| 8 | Custom-field search/filter/columns + mandatory + multi-value | #253/#652/#582 — 5–13 👍 | Partial | M6 |
| 9 | Drag-drop attachments / paste images | #7 — 9 👍, 9c | UI | M8 |
| 10 | Similar-alerts detection | #2065 — 8 👍 | Yes (similar cases/alerts) | M6 |
| 11 | Custom input parameters for responders | Cortex #180 — 7 👍 | Yes | M7 |
| 12 | JIRA integration | #1537 — 7 👍, 8c | No | **M5/M7 — net-new** |
| 13 | Triage notes on alert preview | #1800 — 7 👍 | Partial | M6 |
| 14 | Assign a case to multiple people | #2259 | No | **M6 — net-new** |
| 15 | Remove *delete* permission from R/W users | #2241 | Partial | M2 (finer perms) |
| 16 | SLA / time tracking | #2431 | Partial (KPIs) | M6 (Metrics) |
| 17 | Account-lockout policy | #2311 | Yes | M1 (auth) |
| 18 | ES 8 / OpenSearch 2 support | #2465; Cortex #429 — 47c | Yes (TH5 storage) | **N/A — we use PG/SQLite (+optional OpenSearch)** |
| 19 | COPS playbook support | #756 — 11 👍 | No (TH5 has Functions) | M10 (Functions ≈ automation) |
| 20 | Rewrite the Python SDK | TheHive4py #85 | — | **M3/M8 — net-new (first-party SDK)** |

---

## 3. Pain points & bugs to avoid (the real reason people would switch)

These are not feature requests — they're the friction that made users look for an alternative.
A rewrite that *fixes* these wins adoption even at feature parity.

- **Memory/CPU exhaustion & instability** on Cassandra/JanusGraph — OOM crashes, 100% CPU spikes
  (~every 48h on 4-core/16 GB), "reboot the VM every 2 days." Cortex #214 (47c), #1563, #1341.
  → **Fixed by our PG/SQLite stack** (no Cassandra/JanusGraph).
- **Elasticsearch 8 / OpenSearch 2 incompatibility** trapping orgs on legacy ES. #2465; Cortex
  #429 (47c). → **We avoid the hard ES dependency** (PG/SQLite full-text; OpenSearch optional).
- **Fragile migrations losing alerts** (TH3→TH4). #2188, #2238. → Migration tooling needs
  **contract tests with a no-data-loss assertion** (impl-plan §10).
- **Slow search on large datasets** / slow alert creation (ES indexing). #2116, #1959, #1703. →
  Index design + **keyset pagination** + `limitedCountThreshold` are first-class (M3).
- **Cortex report race / "observable already exists"** — analyzer reports vanish. #1982. →
  Observable dedup on `(dataType,data)` + idempotent connector writes (M6/M7).
- **API v0/v1 confusion.** → Ship **one clean v1 API only**.

**NFR targets that fall out of this:** sub-1s common UI actions; sub-5s search on ~50k cases;
comfortable on modest hardware; zero-dependency dev install (SQLite + uvicorn); production Helm chart.

---

## 4. Integration & ecosystem asks

- **MISP / threat-intel:** pre-create event filtering (#370), bi-directional event update (#2209),
  STIX 2.1 import (#2506). → M7.
- **SOAR / playbooks:** COPS playbook support (#756, 11 👍), better responder parameter templating
  (Cortex #180). → M10 Functions (our automation engine) covers the playbook intent.
- **Ticketing / chat:** JIRA (#1537), and the standard chat sinks. → M5 notifiers + M7 connectors.
- **Webhooks / streaming:** webhooks on case/alert/observable changes; queue sinks. → M5 (Webhook +
  Kafka/Redis notifier sinks).
- **Analyzer ecosystem (Cortex-Analyzers):** better analyzer discovery/store, EML parser, sandbox
  integrations. → covered by keeping the Cortex worker contract (M7) so the existing analyzer
  catalog stays usable.

---

## 5. Must-have vs nice-to-have (by demand)

**Must-have (table-stakes, by signal + competitive landscape):** bulk ops; case timeline/audit;
SAML/OAuth; custom fields with full search; analyzer auto-execution; **modern, light infra (ES8/
OpenSearch or — better — no heavy DB at all)**; solid REST API + Python SDK; RBAC granularity;
MISP; responsive performance.

**Nice-to-have (long-tail):** PDF/dashboard export; Helm charts (blocking only for K8s shops);
COPS playbooks; SLA tracking; email-inbox triage; similar-cases ML; deep JIRA/Slack; triage
scratchpad; multi-value custom fields; responder parameter UI.

(Our plan treats Helm + Python SDK as **must-have** anyway, because they're cheap on this stack and
directly counter the top adoption blockers.)

---

## 6. Reconciliation — where each community ask lands

| Community ask | In TH5? | In our plan? | Home |
|---|---|---|---|
| Case timeline | Yes | Yes (promoted to must-have) | M6 |
| SAML / OAuth / OIDC | Yes | Yes | M9 (consider pulling fwd) |
| Bulk merge / bulk ops | Yes (TH4 3.3.0) | Yes | M6 |
| Custom fields/severities/statuses (full lifecycle) | Partial | Yes | M6 |
| Analyzer auto-run + responder params | Yes | Yes | M6/M7 |
| Similar cases/alerts + observable dedup | Yes | Yes | M6 |
| PDF / case reporting | Yes (Platinum) | Yes | M10 |
| MISP filtering / STIX 2.1 / bi-directional | Partial | Yes | M7 |
| Account lockout / session mgmt / password reset | Yes | Yes | M1/M9 |
| **Helm / K8s-native, light footprint** | Partial | **Yes (net-new emphasis)** | M9 + the whole stack choice |
| **First-party Python SDK** | — | **Yes (net-new)** | M3/M8 |
| **Assign case to multiple people** | No | **Yes (net-new)** | M6 |
| **Finer delete-permission granularity** | Partial | **Yes** | M2 |
| **JIRA / ticketing connector** | No | **Yes (net-new)** | M5/M7 |
| ES8/OpenSearch operational pain | (their fix = TH5 storage) | **Eliminated** (PG/SQLite) | — |

**Net-new beyond *both* TH4 and TH5** (our differentiators, driven by community demand):
lightweight ops (PG/SQLite, no Cassandra/JanusGraph/ES requirement) · production Helm chart ·
first-party Python SDK generated from OpenAPI · multi-assignee · finer-grained delete permissions ·
JIRA/ticketing connector. The first one is the big lever — it's the single most-voiced frustration
and the clearest reason an OSS alternative would gain traction.

---

## Sources

GitHub issue trackers (archived OSS): `TheHive-Project/TheHive`, `Cortex`, `Cortex-Analyzers`,
`TheHive4py`, `Cortex4py`. Issue numbers cited inline (e.g.
[TheHive #84](https://github.com/TheHive-Project/TheHive/issues/84),
[#271](https://github.com/TheHive-Project/TheHive/issues/271),
[#768](https://github.com/TheHive-Project/TheHive/issues/768),
[#1224](https://github.com/TheHive-Project/TheHive/issues/1224),
[Cortex #214](https://github.com/TheHive-Project/Cortex/issues/214)). Competitor deltas in
[`thehive5-feature-gap.md`](./thehive5-feature-gap.md); implementation homes in
[`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md).
