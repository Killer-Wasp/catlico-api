# TheHive 5 — Deep Feature Digest

> Section-by-section deepening of [`thehive5-docs-map.md`](./thehive5-docs-map.md),
> with the detail useful for implementation. Summarised in our own words from the
> official docs (reconstructed via search; `docs.strangebee.com` blocks crawlers
> with 403) — follow the links for authoritative text. New items surfaced here
> are folded into [`thehive5-feature-gap.md`](./thehive5-feature-gap.md).

## 1. Functions — objects, types, methods

[About Functions](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/about-functions/) ·
[Functions Objects](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-functions/functions-objects/)

- **Objects == HTTP API objects.** The objects a function manipulates use the
  *same* input/output shapes as TheHive's REST API — so an implementation where
  the function SDK is a thin client over the API (our recommendation in
  [`thehive5-features-design.md`](./thehive5-features-design.md) §1.3) matches how
  TheHive itself models it.
- **Function types / scopes:**
  - **API** — invoked by an external service via the public API (HTTP call).
  - **Notification** — acts as a notifier; fires on events (case/alert updates).
  - **Action: Case** — run manually in a case's context (responder-like).
  - **Action: Alert** — run manually in an alert's context.
- **Representative methods** (illustrative of the SDK surface):
  `caze.find(query)`, `caze.bulkUpdate({ids,…})`, `caze.unlinkAlert(caseId,alertId)`,
  `caze.mergeSimilarObservables(caseId)`, `task.get(id)`,
  `observable.createInCase(caseId, obs)`, `observable.createInAlert(alertId, obs)`,
  `observable.get(idOrName)`. Methods mirror the API verbs (find/get/create/update/bulk).

## 2. Authentication providers

[Configure Authentication](https://docs.strangebee.com/thehive/administration/authentication/configure-authentication/)
(Platform-management → Authentication tab)

| Provider | Notes | License |
|---|---|---|
| Local DB | built-in | free |
| [LDAP](https://docs.strangebee.com/thehive/administration/authentication/ldap/) / [AD](https://docs.strangebee.com/thehive/administration/authentication/ad/) | OpenLDAP / Microsoft AD | paid; **AD = Platinum** |
| [OAuth 2.0](https://docs.strangebee.com/thehive/administration/authentication/oauth2/) | **deprecated → use OpenID** (Keycloak/Okta/GitHub/M365/Google) | **Platinum** |
| OpenID Connect | preferred SSO | **Platinum** |
| [SAML](https://docs.strangebee.com/thehive/administration/authentication/saml/) | Okta / Microsoft Entra ID | **Platinum** |
| Basic, HTTP header (reverse-proxy SSO) | | paid |
| API key | normal + service accounts | free-ish |
| **MFA** | multi-factor on top of a primary provider | |

Providers are **chained** (as in TH4's `MultiAuthSrv`). Non-local providers need a
paid license; AD/OAuth2/OpenID/SAML need **Platinum**.

## 3. Dashboards — widget types

[Widgets in Dashboards](https://docs.strangebee.com/thehive/user-guides/analyst-corner/dashboard/widgets-dashboards/)

A **row** widget is required first; each row holds ≤3 widgets. Confirmed widget
types (all aggregation-driven over the `/query` engine):

- **Bar** — grouped vertical bars; optional **stacked** mode.
- **Donut** — proportions; a "max segments" cap rolls the remainder into an **Other** slice.
- **Line** — series over a time axis (trends).
- **Gauge** — a single value against a scale/threshold (KPIs/SLAs).
- **Text** — rich text / aggregated values in **TheHive-flavored Markdown**.
- (plus table/counter-style displays)

Dashboards are **org-scoped**, **private or shared**, and moved between orgs by
export/import.

## 4. Notifications — triggers & notifiers

[About Notifications](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/about-notifications/)
— one **trigger**, many **notifiers**.

- **Triggers** (event types): `AnyEvent`, `FilteredEvent`, `CaseCreated`,
  `CaseClosed`, `CaseFlagged`, `CaseShared`, `AlertCreated`, `AlertClosed`,
  `AlertImported`, `ObservableCreated`, `CaseObservableCreated`,
  `AlertObservableCreated`, `TaskClosed`, `TaskMandatory`, `TaskAssigned`,
  `LogInMyTask`, `JobFinished`, `ActionFinished`.
- **Notifiers:** `EmailerToUser`, `EmailerToAddr`, `HttpRequest`,
  [`Webhook`](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/webhook/),
  [`Mattermost`](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/mattermost/),
  [`Slack`](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/slack/),
  `Teams`, **[`Kafka`](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/kafka/)**,
  **[`Redis`](https://docs.strangebee.com/thehive/user-guides/organization/configure-organization/manage-notifications/notifiers/redis/)**.
  *(Kafka/Redis are stream/queue sinks — TH5 can publish events to a bus, not just chat/HTTP.)*
- A per-notification toggle controls "send to every user in the org" vs targeted.

## 5. Observables

[About Observables](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/observables/about-observables/) ·
[Run Analyzers](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/observables/run-analyzers-on-an-observable/)

- **Flags:** **IOC** (linked to malicious activity), **sighted** (seen in the
  environment), **ignore-similarity** (exclude from the similar-alerts/cases
  algorithm — e.g. your own corporate domain).
- **Type** determines (a) which Cortex analyzers are offered and (b) whether the
  observable carries a **value** or an **attachment**. [Custom observable types](https://docs.strangebee.com/thehive/administration/observable-types/create-an-observable-type/)
  extend the predefined list.
- **Run analyzers:** only analyzers matching the observable's **type + TLP + PAP**
  are offered. Reports attach as verdict tags; similarity surfaces in Similar
  alerts / Similar cases tabs.

## 6. Profiles & permissions

[About Profiles](https://docs.strangebee.com/thehive/administration/profiles/about-profiles/)

- **Three profile classes:** **Administration** (Admin org only; platform-wide),
  **Organization** (operational: cases/alerts/observables…), and **External**
  (for external stakeholders — see §10).
- **Predefined profiles:** `Admin`, `Org-Admin`, `Read-Only`, `External-Reader`,
  `External-Actor` are **immutable**; `Analyst` is editable/deletable.
- Permissions are **cumulative**; a **profile is assigned per organisation**
  (matches the membership model in [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md)).
- New TH5 permission: `manageCaseAccess/external` (share cases with external
  stakeholders).

## 7. Cortex integration

[About Cortex](https://docs.strangebee.com/thehive/administration/cortex/about-cortex/)

- **Analyzers** enrich observables → report. **Responders** act on **cases,
  alerts, observables, tasks, and task logs** → report.
- Analyzer availability gated by observable **type + TLP + PAP**.
- Connect **multiple** Cortex servers (paid license); managed by an admin profile
  with `managePlatform`.

## 8. MISP integration

[About MISP](https://docs.strangebee.com/thehive/administration/misp-integration/about-misp-integration/) ·
[Connect a MISP Server](https://docs.strangebee.com/thehive/administration/misp-integration/connect-a-misp-server/) ·
[Export a Case to MISP](https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/export-a-case-to-misp/)

- **Import:** auto-pull events → **alerts** (multi-server, paid), or manual import
  event → case.
- **Filters** on import: max **age** (creation date); **include**/**exclude** by
  MISP org; **max attributes** per event; **include**/**exclude** by **tags**.
- **Export:** manual, **IOC-flagged observables only** → a MISP event; options to
  export observable **tags** and a **case link**; after export, **IOC updates
  auto-sync** to MISP.

## 9. API & search

[API docs (OpenAPI)](https://docs.strangebee.com/thehive/api-docs/) ·
[Filtering and Sorting](https://docs.strangebee.com/thehive/user-guides/analyst-corner/about-filtering-and-sorting/)

- Bearer API-key auth; JSON query objects; filters AND-combined; wildcard `*`.
- This is the same `/query` + field-registry engine documented in
  [`thehive-api-internals.md`](./thehive-api-internals.md) Part 2.

## 10. NEW — features surfaced in this deep pass (not previously captured)

These are genuine TheHive 5 capabilities **beyond** what the earlier gap doc listed:

| Feature | What it is | Recommendation |
|---|---|---|
| **TheHive Portal / External profiles** | Share a case with **external stakeholders** (e.g. a customer/victim org) via a restricted portal; `External-Reader` (view) and `External-Actor` (act) profiles + `manageCaseAccess/external`. [Set up portal access](https://docs.strangebee.com/thehive/administration/thehive-portal/set-up-thehive-portal-access/) | **Implement-later** — extends the Share model to an "external" share class + a constrained portal UI |
| **Kafka / Redis notifiers** | Publish events to a **message bus / stream**, not just chat/HTTP — for SOAR pipelines and downstream consumers | **Implement-later** — extra notifier plugins behind the outbox (M5) |
| **Custom case/alert statuses** | User-defined statuses beyond the built-in `Open/Resolved/Duplicated` (case) and read/triaged (alert) | **Implement-core** — a `custom_status` lookup per org; small but expected |
| **OAuth2 deprecated → OpenID** | Prefer an OIDC provider over the legacy OAuth2 one | note for M9 auth work |

These are reflected in the gap doc's addendum and roadmap notes.
