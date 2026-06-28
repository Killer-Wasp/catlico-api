# TheHive 5 (StrangeBee) — Documentation Map & Digest

> A structured capture of the official TheHive 5 documentation at
> [docs.strangebee.com/thehive](https://docs.strangebee.com/thehive/overview/),
> organised by the doc site's navigation, with the URL and a short digest for
> each area.
>
> **Provenance / caveat:** `docs.strangebee.com` hard-blocks automated fetches
> (HTTP 403 / Cloudflare) on every page *and* the sitemap, so this is **not** a
> verbatim page-by-page crawl. It was reconstructed from search engine results
> (titles, URLs, snippets) on 2026-06. Treat it as a **map + digest with source
> links**, not a copy of StrangeBee's (copyrighted) docs — follow the links for
> authoritative text. Companion analysis: [`thehive5-feature-gap.md`](./thehive5-feature-gap.md),
> [`thehive5-features-design.md`](./thehive5-features-design.md).

## Top-level structure

```
TheHive 5 docs
├── Overview
├── Installation        (Docker, packages, cluster, upgrade-from-4.x, licenses)
├── Operations          (backup/restore, monitoring, performance, troubleshooting)
├── User Guides
│   ├── Analyst Corner  (Cases, Alerts, Tasks, Dashboards, Filtering & Sorting)
│   └── Organization    (configure-organization: functions, notifications, templates, users)
├── Administration      (orgs, users/profiles, custom fields, observable types,
│                        authentication, Cortex, MISP, analyzer templates, license, platform)
├── API documentation   (OpenAPI)
└── Release Notes       (5.0 → 5.5)
```

---

## 1. Overview
- [Overview](https://docs.strangebee.com/thehive/overview/) — TheHive is a security **case-management** platform for SOC / CSIRT / CERT / MSSP teams covering the full incident lifecycle: alert triage → case management → investigation → response → reporting. Built around a flexible **template engine** with **metrics** and **custom fields**.

## 2. Installation & Licensing
- [Deploy with Docker](https://docs.strangebee.com/thehive/installation/docker/)
- [Install with Packages (standalone Linux)](https://docs.strangebee.com/thehive/installation/installation-guide-linux-standalone-server/)
- [Set Up a Cluster with Packages](https://docs.strangebee.com/thehive/installation/deploying-a-cluster/) — 3 active nodes: **Cassandra + Elasticsearch + TheHive**; shared **file storage via NFS**.
- [Upgrade from Version 4.x](https://docs.strangebee.com/thehive/installation/upgrade-from-4.x/)
- [About Licenses](https://docs.strangebee.com/thehive/installation/licenses/about-licenses/) · [Activate/Update a License](https://docs.strangebee.com/thehive/installation/licenses/license/)

**Stack roles:** Cassandra = durable/consistent primary store (3 nodes, RF=3, single-node-failure tolerant; `cqlsh` needs Python 3.9). Elasticsearch = indexing/fast search (audit logs in Cassandra by default, switchable to ES for volume). File storage = case/alert/org attachments (same NFS mount on all nodes).

**License tiers** ([about-licenses](https://docs.strangebee.com/thehive/installation/licenses/about-licenses/)): **Community** (free, limited users/features), **Gold** (most use cases, no strict compliance), **Platinum** (HA/scale + enterprise SSO: AD/OAuth2/OpenID/SAML), **MSSP** (partners/cloud/service providers). 14-day Platinum trial → **read-only** when it expires. Read-only and unlicensed-permission users are free.

## 3. Operations
Root: [Backup & Restore](https://docs.strangebee.com/thehive/operations/backup-restore/restore/restore-hot-backup/)
- **Hot backup/restore** — standalone & cluster ([standalone](https://docs.strangebee.com/thehive/operations/backup-restore/backup/hot-backup/hot-backup-standalone-server/), [restore on cluster](https://docs.strangebee.com/thehive/operations/backup-restore/restore/hot-restore/restore-hot-backup-cluster/)); coordinate Cassandra + ES + file-storage backups together (cron) to minimise inconsistency; restore needs app stop + maintenance window.
- **Cold restore** (e.g. [Docker Compose](https://docs.strangebee.com/thehive/operations/backup-restore/restore/cold-restore/docker-compose/)).
- Also: monitoring setup, performance optimisation, troubleshooting, Cassandra cluster operations.

## 4. User Guides — Analyst Corner

### 4.1 Cases
- [About Cases](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/about-cases/) · [Create a Case](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/create-a-new-case/)
- **Observables** — [About](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/observables/about-observables/) · [Add](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/observables/add-an-observable/) (IPs, hashes, domains, emails; IOC flag; used for similarity)
- **Tasks** — analyst-assigned actions (analyse/assess/mitigate) with task logs
- **TTPs** — MITRE ATT&CK techniques attached to the case
- **Comments**, **Attachments**
- **Timeline** — [View a Case Timeline](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/case-timelines/view-case-timeline/) — custom events, TTPs, logs, tasks, alerts on a timeline; add custom events, export JSON, zoom, graph/list views
- **Case Reports** — [About](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/case-reports/about-case-reports/) · [Save/Download](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/case-reports/save-download-a-case-report/) — template → Markdown/printable-HTML (Platinum)
- **Search** — [Find a Case](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/search-for-cases/find-a-case/) · [Find an Observable](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/search-for-cases/find-an-observable/) · [Find Similar Alerts/Cases](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/find-similar-alerts-cases/) (by shared observables)

### 4.2 Alerts
- [About Alerts](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/about-alerts/) — events from SIEM/IDS/EDR/firewall/MISP; triaged then closed or converted to a case. Carry observables, TTPs, tags, comments, severity, TLP/PAP, custom fields, custom statuses.
- [Preview Alerts](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/alerts-description/preview-alerts/) · [View Alert Details](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/general/)
- [Create a Case from an Alert](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/create-a-case-from-an-alert/) · [Add an Alert to an Existing Case](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/add-an-alert-to-an-existing-case/) — transfers observables/TTPs/attachments/comments/custom fields
- [Search methods](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/search-for-alerts/overview-search-methods-alert/)

### 4.3 Tasks
- [Find a Task](https://docs.strangebee.com/thehive/user-guides/analyst-corner/tasks/search-for-tasks/find-a-task/) and the task list/board.

### 4.4 Dashboards
- [About Dashboards](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/about-dashboards/) — org-level; **private or shared**; not shared across orgs (export/import to move).
- [Widgets in Dashboards](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/widgets-dashboards/) — a **row** widget is required first (≤3 widgets/row); types include **vertical bar, line chart, table, text** (TheHive-flavored Markdown), plus counters/donut/heatmap (~8 total).
- [Create a Dashboard](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/create-a-dashboard/) · [Add/Remove Widgets](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/add-remove-widgets-dashboard/)

### 4.5 Filtering & Sorting / global search
- [About Filtering and Sorting](https://docs.strangebee.com/thehive/user-guides/analyst-corner/about-filtering-and-sorting/) — filters AND-combined; **wildcard `*`** search; "All elements" searches across cases/alerts/observables/jobs/tasks/task-logs.
- [Org-wide Timeline](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases-list/timeline/)

## 5. User Guides — Organization configuration
Path: `user-guides/organization/configure-organization/…`

### 5.1 Functions (automation)
- [About Functions](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/about-functions/) · [Create a Function](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/create-a-function/) · [Functions Objects](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/functions-objects/)
- Automate manual/repetitive tasks **on a schedule or on conditions**, or triggered by **events** (notifications), **manually** from a case/alert (like a responder), or via **API/HTTP**. Functions get **objects** exposing methods to read/mutate cases/alerts/tasks/observables, and can invoke analyzers/responders.

### 5.2 Notifications
- [About Notifications](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/about-notifications/) — a notification has **one trigger, many notifiers**.
- [Write a FilteredEvent Trigger](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/write-filtered-event-trigger/)
- **Notifiers**: `EmailerToUser`, `EmailerToAddr`, `HttpRequest`, `Mattermost`, [Teams](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/teams/), Slack.

### 5.3 Templates
- **Case templates** — [create](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-templates/case-templates/create-a-case-template/) · [export/import](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-templates/case-templates/export-import-a-case-template/)
- **Case report templates** — [about](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-templates/case-report-templates/about-case-report-templates/) · [edit](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-templates/case-report-templates/edit-a-case-report-template/) (text/image/table/list widgets)

### 5.4 User accounts (org level)
- [About User Accounts](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-user-accounts/about-user-accounts/) — **Normal** accounts (web UI, all auth methods, optional API key) vs **Service** accounts (API-key only). A user can belong to multiple orgs and switch between them.

## 6. Administration

### 6.1 Organizations & access
- [About Organizations](https://docs.strangebee.com/thehive/administration/organizations/about-organizations/) — a default **admin** org manages global config; orgs are tenant boundaries.
- Users, **Profiles** (permission sets), org-to-org links/sharing.

### 6.2 Custom fields & entities
- [About Custom Fields](https://docs.strangebee.com/thehive/administration/custom-fields/about-custom-fields/) · [Manage](https://docs.strangebee.com/thehive/administration/custom-fields/manage-a-custom-field/) · [Create](https://docs.strangebee.com/thehive/administration/custom-fields/create-a-custom-field/) — extend case/alert fields; mandatory/optional, predefined values, groups.
- Observable types, custom statuses (entities management).

### 6.3 Authentication
- [Configure Authentication](https://docs.strangebee.com/thehive/administration/authentication/configure-authentication/) — local DB, **LDAP/AD**, **SSO via SAML/OpenID**, **OAuth2**, API key, Basic, **HTTP header**, **MFA**.
  - [OAuth 2.0](https://docs.strangebee.com/thehive/administration/authentication/oauth2/) · [SAML](https://docs.strangebee.com/thehive/administration/authentication/saml/)
  - **License gating:** non-local providers need a paid license; **AD/OAuth2/OpenID/SAML need Platinum**.

### 6.4 Integrations
- **Cortex** — [About Cortex](https://docs.strangebee.com/thehive/administration/cortex/about-cortex/) — connect ≥1 Cortex (multiple needs paid license); analyzers enrich observables, responders act on cases/alerts/observables/tasks/task-logs; managed by an admin profile with `managePlatform`.
- **MISP** — [About](https://docs.strangebee.com/thehive/administration/misp-integration/about-misp-integration/) · [Connect a MISP Server](https://docs.strangebee.com/thehive/administration/misp-integration/connect-a-misp-server/) — auto-import events; manual export of IOC-flagged observables; multiple servers need paid license; `managePlatform`.
- **Analyzer templates** — [About](https://docs.strangebee.com/thehive/administration/analyzer-templates/about-analyzer-templates/) · [Import](https://docs.strangebee.com/thehive/administration/analyzer-templates/import-analyzer-templates/) · [Customize](https://docs.strangebee.com/thehive/administration/analyzer-templates/customize-an-analyzer-template/) — HTML templates controlling how analyzer reports render.

### 6.5 License & platform
- [License Management](https://docs.strangebee.com/thehive/administration/license/) — activate/update; user-count + org-count drive pricing.

## 7. API
- [TheHive 5 API Documentation](https://docs.strangebee.com/thehive/api-docs/) — **OpenAPI** spec for routes.
- **Auth:** Bearer API key (`Authorization: Bearer <API_KEY>`).
- **Search/query:** JSON query objects (`{ "query": {...} }`), filters AND-combined, wildcard `*`. Mirrors the v1 `/query` engine described in [`thehive-api-internals.md`](./thehive-api-internals.md).

## 8. Release Notes (feature provenance)
- [5.0](https://docs.strangebee.com/thehive/release-notes/release-notes-5.0/) — TheHive 5 GA: timeline, dashboards revamp, metrics/KPIs, comments, notifications, alert pre-processing.
- [5.1](https://strangebee.com/blog/thehive-5-1-new-features/) — **SAML** SSO; URL custom-field type.
- [5.2](https://docs.strangebee.com/thehive/release-notes/release-notes-5.2/) — **Case reporting** (template → Markdown/HTML).
- [5.3](https://strangebee.com/blog/thehive-5-3-is-out-and-buzzing-for-even-more-efficiency/) — efficiency improvements.
- [5.4](https://docs.strangebee.com/thehive/release-notes/release-notes-5.4/) — **Functions** maturation (function notifier, analyzers/responders from functions).
- [5.5](https://docs.strangebee.com/thehive/release-notes/release-notes-5.5/) — functions manually triggerable from case/alert like responders; further automation.

## 9. How this maps to our rewrite docs
- Feature delta vs OSS TheHive 4 → [`thehive5-feature-gap.md`](./thehive5-feature-gap.md)
- Mechanism design for the net-new features → [`thehive5-features-design.md`](./thehive5-features-design.md)
- Roadmap placement → [`thehive4-roadmap.md`](./thehive4-roadmap.md) (M3/M5/M6/M9/M10)

---

### Sources
StrangeBee TheHive 5 documentation (docs.strangebee.com) and blog (strangebee.com),
accessed via search 2026-06; direct crawl blocked (403). Key entry points:
[Overview](https://docs.strangebee.com/thehive/overview/),
[Installation](https://docs.strangebee.com/thehive/installation/docker/),
[Backup & Restore](https://docs.strangebee.com/thehive/operations/backup-restore/restore/restore-hot-backup/),
[Cases](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/about-cases/),
[Alerts](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/about-alerts/),
[Dashboards](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/about-dashboards/),
[Functions](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/about-functions/),
[Notifications](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/about-notifications/),
[Authentication](https://docs.strangebee.com/thehive/administration/authentication/configure-authentication/),
[Cortex](https://docs.strangebee.com/thehive/administration/cortex/about-cortex/),
[MISP](https://docs.strangebee.com/thehive/administration/misp-integration/about-misp-integration/),
[Licenses](https://docs.strangebee.com/thehive/installation/licenses/about-licenses/),
[API docs](https://docs.strangebee.com/thehive/api-docs/).
