# TheHive 5 (commercial) — Competitor Feature Gap & What to Implement

> Our data-model, parity spec, and roadmap are reverse-engineered from the
> **open-source TheHive 4** (the last AGPL release). The current product —
> **TheHive 5 by StrangeBee** ([docs.strangebee.com](https://docs.strangebee.com/thehive/overview/)) —
> is closed-source and has added several capabilities on top of the TH4 feature
> set. This doc inventories that **delta**, says what we should build to stay
> competitive, and points each item at its home in
> [`thehive4-roadmap.md`](./thehive4-roadmap.md).
>
> Lens (same as the rest of the docs): **Implement-core / Implement-later /
> Defer / Skip** — we are matching the *product shape*, not cloning a licensed
> product byte-for-byte.
>
> **Companion docs:** this is the *competitor* axis (what TH5 added beyond TH4). For the *demand*
> axis (what OSS users actually requested), see
> [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md); for
> design changes that improve on TheHive rather than clone it, see
> [`thehive5-design-improvements.md`](./thehive5-design-improvements.md).

## How TheHive 5 differs from the OSS TheHive 4 we modelled

TheHive 5 keeps the TH4 spine (organisations + sharing, cases/tasks/observables/
alerts, custom fields, templates, tags/taxonomies, MITRE TTPs, Cortex + MISP,
dashboards, audit/stream, Pages, attachments) — **all already in our plan** — and
layers on automation, investigation-UX, reporting, and enterprise-auth features.
It also moved storage to Cassandra + Elasticsearch and ships commercial tiers
(Gold/Platinum), backup/restore, and cluster tooling.

## Gap table (TH5 additions vs our TH4-based plan)

| TheHive 5 feature | Added in | In our plan today? | Recommendation | Roadmap home |
|---|---|---|---|---|
| **Functions** — automation engine (scheduled / event / manual-from-case-alert / API-triggered) with an objects SDK to manipulate cases, alerts, tasks, observables; can invoke analyzers/responders | 5.4–5.5 | ❌ No | **Implement-core** | **new M10** |
| **Case Timeline** — visual case-lifecycle view | 5.0 | ❌ No | **Implement-core** (cheap on top of audit events) | M6 |
| **Metrics / KPIs** — numeric, per-template metrics distinct from custom fields, for SLA/reporting | 5.0 | ⚠️ Partial (custom fields only) | **Implement-core** | M6 |
| **Comments** on cases & alerts (TH4 only had task logs) | 5.0 | ❌ No | **Implement-core** | M6 |
| **Alert pre-processing** — run analyzers on alert observables, add comments/TTPs/KPIs before promotion; alert preview | 5.0+ | ⚠️ Partial (alert→case only) | **Implement-core** | M6 / M7 |
| **Observable dedup on alert import** + **Similar alerts / Similar cases** tabs | 5.x | ⚠️ Partial (similarity noted) | **Implement-core** | M6 |
| **Notifiers: Slack, MS Teams, generic HTTP request** (TH4 had Email/Webhook/Mattermost/AppendToFile) | 5.x | ⚠️ Partial | **Implement-core** | M5 |
| **SAML SSO** | 5.1 | ❌ No (had OAuth2/OIDC/LDAP/PKI/header) | **Implement-core** | M9 |
| **Session management** (view/revoke active sessions) + **self-service password reset** + **LDAP/AD directory sync** | 5.x | ❌ No | **Implement-core** | M9 |
| **OpenAPI spec** auto-generation for the API | 5.x | ❌ No | **Implement-core** (cheap, high value) | M3 |
| **Dashboards revamp** — 8 widget types, private vs shared, drag-and-drop builder (org-scoped) | 5.0 | ⚠️ Basic dashboards only | **Implement-later** (richen M6 widgets) | M6 |
| **Custom field types** beyond TH4 (e.g. URL) | 5.1 | ⚠️ Partial | **Implement-later** | M6 |
| **Case Reporting** — report-template library → Markdown/printable-HTML with dynamic widgets (text/image/table/list) | 5.2 | ❌ No | **Implement-later** (Platinum-tier in TH5; valuable but not MVP) | **new M10** |
| **Commercial licensing tiers** (Gold/Platinum, feature gating) | 5.0 | ❌ No | **Skip** (not relevant to an internal build) | — |
| **Cassandra cluster ops / ES index ops** tooling | 5.0 | ❌ No | **Skip / N/A** (our stack is PostgreSQL + OpenSearch) | M9 (own ops) |

## The big one: **Functions** (TheHive 5's flagship automation)

This is the largest genuine gap and the main reason TH5 feels more capable than
TH4. In TH4, automation = the Cortex/MISP connectors + notification triggers. In
TH5, **Functions** generalize that into a first-class automation engine:

- **Trigger modes:** on a schedule (cron-like), on an internal **event**
  (notification triggers, incl. `FilteredEvent`), **manually** from a case/alert
  (exactly like a responder), or via an **API/HTTP** call.
- **Function objects SDK:** functions get predefined objects exposing methods to
  read/mutate cases, alerts, tasks, observables, etc. — i.e. the same
  service/audit/RLS path as a user action.
- **Composition:** a function can invoke Cortex analyzers/responders, send
  notifications, or call external HTTP.

For our rewrite this maps cleanly onto infrastructure we already planned: it is a
**trusted internal writer** that goes through the normal service → audit → outbox
→ RLS path (the same contract as connectors, see parity-spec §7/§8). The new work
is the **trigger scheduler**, the **sandboxed function runtime**, and the
**objects SDK / permissions for functions**. Treat function execution as a
distinct principal with its own profile so RLS still applies.

> Security note for a rewrite: a scriptable automation engine that can mutate data
> and call outbound HTTP is a privilege-escalation and SSRF surface. Run functions
> with a scoped service identity (not the triggering user's full rights), pin
> outbound egress, and audit every function-initiated mutation like any other.

## What we are explicitly **not** copying

- **Licensing/tier gating** — irrelevant for an internal platform.
- **Cassandra/Elasticsearch-specific operational tooling** — our stack is
  PostgreSQL + OpenSearch (see parity spec); we build the equivalent ops for that.
- **Exact TH5 UI layout / wording** — we match capability, not pixels.

## Net effect on the plan

- **One new milestone (M10)** for the automation engine (Functions) + Case
  Reporting.
- **Additions to existing milestones:** Timeline, Metrics/KPIs, Comments, alert
  pre-processing, observable dedup-on-import (M6); Slack/MS Teams/HTTP notifiers
  (M5); SAML, session management, password reset, directory sync (M9); OpenAPI
  (M3).
- **Calendar impact:** roughly **+3–4 months** of parallelizable work on top of
  the previous estimate; none of it is on the serial spine (M0–M4), so it extends
  breadth, not the critical path.

## Addendum — features surfaced in the deep documentation pass

A deeper read (see [`thehive5-docs-deep.md`](./thehive5-docs-deep.md)) turned up
three more TheHive 5 capabilities not listed above:

| Feature | What it adds | Recommendation | Roadmap home |
|---|---|---|---|
| **TheHive Portal / External profiles** | share a case with **external stakeholders** via a constrained portal; `External-Reader`/`External-Actor` profiles + `manageCaseAccess/external` | **Implement-later** — an "external" share class on the Share model + a restricted portal UI | M10 |
| **Kafka / Redis notifiers** | publish events to a **stream/queue** (SOAR pipelines), not just chat/HTTP | **Implement-later** — extra notifier plugins behind the outbox | M5 |
| **Custom case/alert statuses** | user-defined statuses beyond the built-in enums | **Implement-core** — per-org `custom_status` lookup | M6 |

(OAuth2 is deprecated in TH5 in favour of OpenID — a note for the M9 auth work.)

---

*Sources (TheHive 5 documentation & release notes, StrangeBee):
[Overview](https://docs.strangebee.com/thehive/overview/),
[About Functions](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/about-functions/),
[About Notifications](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/about-notifications/),
[About Case Reports](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/case-reports/about-case-reports/),
[Analyzer Templates](https://docs.strangebee.com/thehive/administration/analyzer-templates/about-analyzer-templates/),
[Release notes 5.0–5.5](https://docs.strangebee.com/thehive/release-notes/release-notes-5.5/),
[5-year/3-year feature roundup](https://strangebee.com/blog/thehive-5-3-year-anniversary-key-features/).*
