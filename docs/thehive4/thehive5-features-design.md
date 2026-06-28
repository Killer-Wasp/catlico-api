# TheHive 5 parity — detailed feature & mechanism design

> Mechanism-level design for the **net-new TheHive 5 features** identified in
> [`thehive5-feature-gap.md`](./thehive5-feature-gap.md). For each: data model,
> API surface, execution mechanism, permissions/RLS, and edge cases. The **core
> TheHive 4 mechanisms** (request pipeline, `/query` DSL, audit→outbox→stream,
> Share/RLS tenancy) are already specified in
> [`thehive-api-internals.md`](./thehive-api-internals.md),
> [`thehive4-parity-spec.md`](./thehive4-parity-spec.md), and the
> [`poc-postgres/`](./poc-postgres/) PoC — this doc builds **on** them and reuses
> them rather than re-deriving them.

Conventions used below: tables carry the standard `created_at/by`,
`updated_at/by` metadata; every readable table gets an RLS policy keyed on
`membership`/`case_share` (parity-spec §3); every mutation flows through the
service → audit → **outbox** → RLS path (api-internals Part 3).

---

# 1. Functions — the automation engine (the centerpiece)

The single largest gap vs TheHive 5. In TH4, automation = Cortex/MISP connectors
+ notification triggers. **Functions** generalize that into a first-class,
user-authored automation runtime.

## 1.1 What a Function is, and its four trigger modes

A **Function** = named, versioned, org-scoped automation code plus a binding that
says *when it runs*:

| Trigger mode | Fires when | Analogous TH4 thing |
|---|---|---|
| **Scheduled** | a cron expression elapses | (none) |
| **Event** | an internal event matches a `FilteredEvent` filter (case created, observable flagged IOC, task assigned…) | notification trigger |
| **Manual** | an analyst clicks "Run" on a case/alert | a Cortex **responder** |
| **API** | an external `POST /function/{id}/run` call | webhook-in |

A function body can: read/mutate cases, alerts, tasks, observables (via the
**objects SDK**, §1.5); call Cortex analyzers/responders; send notifications;
make **allowlisted** outbound HTTP.

## 1.2 Data model

```sql
CREATE TABLE function (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id BIGINT NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT 'js',     -- runtime selector
    source          TEXT NOT NULL,                   -- the code (or a ref to a blob)
    version         INT  NOT NULL DEFAULT 1,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    timeout_ms      INT  NOT NULL DEFAULT 30000,
    config          JSONB NOT NULL DEFAULT '{}',     -- non-secret params
    secrets_ref     TEXT,                            -- pointer to a secret store entry, never inline
    profile_id      BIGINT NOT NULL REFERENCES profile(id),  -- the SCOPED principal it runs as
    UNIQUE (organisation_id, name)
);

CREATE TABLE function_trigger (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    function_id BIGINT NOT NULL REFERENCES function(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL,                 -- 'scheduled' | 'event' | 'manual' | 'api'
    cron        TEXT,                          -- mode=scheduled
    event_filter JSONB,                        -- mode=event: a FilteredEvent predicate (reuses /query operators)
    entity_types TEXT[],                       -- mode=manual: which entities the "Run" button appears on
    enabled     BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE function_run (             -- execution log + idempotency + retries
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    function_id   BIGINT NOT NULL REFERENCES function(id) ON DELETE CASCADE,
    trigger_mode  TEXT NOT NULL,
    dedup_key     TEXT,                        -- idempotency (e.g. event id + function id)
    status        TEXT NOT NULL DEFAULT 'queued', -- queued|running|success|failure|timeout|cancelled
    context_type  TEXT, context_id BIGINT,     -- the case/alert it ran against (manual/event)
    input         JSONB,
    output        JSONB,
    error         TEXT,
    attempts      INT NOT NULL DEFAULT 0,
    started_at    TIMESTAMPTZ, ended_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON function_run (function_id, dedup_key) WHERE dedup_key IS NOT NULL;
```

RLS: `function`/`function_run` are org-scoped exactly like cases. `secrets_ref`
never holds the secret — it points at a vault/KMS entry resolved at run time.

## 1.3 Execution mechanism — the key design decision

User-authored code that **mutates data and makes outbound calls** is the riskiest
thing in the whole system. The clean mechanism that reuses everything we already
built:

> **Run the function in an isolated worker, and make the objects SDK a thin
> client over our own REST API authenticated with a short-lived, scoped token.**

Consequences of that one choice:
- The worker is the **sandbox boundary** (separate process/container — reuse the
  Cortex job-runner pattern: process / docker / k8s).
- Because the SDK calls the *normal* API as a **function principal**, every
  mutation automatically gets **permission checks + RLS + audit + outbox
  fan-out** for free. No second enforcement path to keep in sync.
- The token is minted per run, scoped to `function.profile_id`, the function's
  org, and (for manual/event) the **specific context entity**, and expires at
  `timeout_ms`.

Runtime options (the `language`/runner selector):

| Runtime | Isolation | Latency | Use for |
|---|---|---|---|
| Embedded JS isolate (GraalJS / V8 isolate / QuickJS) | in-process, must disable host access | low | trusted, admin-authored, CPU-light functions |
| External container worker (Cortex-style) | process/cgroup/seccomp | higher | untrusted or heavy functions; default |

**Recommendation:** external container worker by default; allow an embedded
isolate only for admin-authored functions, behind config. Either way the SDK ==
scoped REST client, so the data-access guarantees are identical.

## 1.4 Trigger dispatch

- **Scheduled:** a single leader-elected **scheduler** (or `pg_cron` / a job
  table polled with `SELECT … FOR UPDATE SKIP LOCKED`) inserts a `function_run`
  (`status=queued`) when a cron fires. Leader election prevents double-fire in a
  cluster.
- **Event:** functions subscribe to the **same outbox** the notification engine
  drains (api-internals §3.3). On each committed event, evaluate every enabled
  `event` trigger's `event_filter` (reuse the `/query` predicate engine) and
  enqueue a `function_run` with `dedup_key = event_id:function_id` (the unique
  index makes redelivery idempotent).
- **Manual:** the UI shows a "Run function" button (gated by `manageFunction` +
  the function's `entity_types`); clicking enqueues a run bound to that entity —
  identical UX to a responder.
- **API:** `POST /function/{id}/run` enqueues a run; permission-checked like any
  endpoint.

A pool of **dispatchers** claims queued runs (`FOR UPDATE SKIP LOCKED`), mints
the scoped token, invokes the worker, records `output`/`error`/`status`, and
retries with backoff up to a cap (then `status=failure`). Runs are **at-least
once**; the `dedup_key` + idempotent SDK writes make that safe.

## 1.5 The objects SDK surface

What the function code can call (all of it → our REST API as the function
principal, so all of it is permission/RLS/audit-gated):

```
ctx.case.get(id) / .update(id, fields) / .addTask() / .addComment() / .addTTP() / .setMetric()
ctx.alert.get(id) / .update() / .promoteToCase(templateId) / .markRead()
ctx.observable.list(caseId) / .create() / .flagIoc() / .addTag()
ctx.task.create() / .complete() / .addLog()
ctx.cortex.runAnalyzer(observableId, analyzerId) / .runResponder(entity, responderId)
ctx.notify.webhook(url, body) / .email(to, subj, body) / .slack(channel, msg)
ctx.http.fetch(req)          // ONLY via the egress proxy + allowlist (§1.6)
ctx.input                    // trigger payload (event/manual context, api body)
ctx.config / ctx.secrets     // resolved config + vault-resolved secrets
ctx.log(...)                 // captured into function_run.output
```

## 1.6 Security model (non-negotiables)

A scriptable engine that writes data and calls the network is a
privilege-escalation + SSRF surface. Required controls:

- **Scoped principal:** runs as `function.profile_id`, **never** the triggering
  user's full rights. Admin-scope permissions are not grantable to functions.
- **Egress control:** `ctx.http.fetch` goes through an **egress proxy with a
  per-function allowlist**; block RFC1918/link-local/metadata IPs (169.254.169.254)
  to kill SSRF; no raw sockets.
- **Resource limits:** CPU/memory caps, wall-clock `timeout_ms`, output size cap,
  max API calls per run (rate-limit the SDK token).
- **No host access:** no filesystem, no env, no spawning processes (enforced by
  the worker sandbox).
- **Secrets:** injected from a vault by reference at run time, redacted from
  `function_run` logs.
- **Full audit:** every function-initiated mutation is audited as the function
  principal with the `function_run.id` in the audit details — so the activity
  flow shows "Function X changed this", not a mystery write.
- **Approval/versioning:** editing a function bumps `version`; optionally require
  a second approver before `enabled=true` (4-eyes for automation that can mutate
  prod data).

## 1.7 Failure, retry, idempotency, observability

- Idempotency: `dedup_key` unique index + SDK writes that are safe to replay
  (prefer upserts / "add if absent").
- Retries: dispatcher backoff (2s/4s/8s…) to a cap; then `failure` + optional
  notification.
- Timeouts: worker killed at `timeout_ms` → `status=timeout`.
- Observability: `function_run` is the execution log (input/output/error/timing/
  attempts); surfaced in the UI and queryable via `/query`.

## 1.8 Sequence — an event-triggered function

```mermaid
sequenceDiagram
    autonumber
    participant TX as Service tx (some mutation)
    participant OB as Outbox (post-commit)
    participant FD as Function dispatcher
    participant W as Function worker (sandbox)
    participant API as Platform REST API
    TX->>OB: commit → event row (e.g. observable.flaggedIoc)
    OB->>FD: deliver event
    FD->>FD: match event_filter; enqueue function_run (dedup_key)
    FD->>FD: mint scoped token (profile, org, context, ttl)
    FD->>W: run(source, input, token)
    W->>API: ctx.case.addTag(...)  (Bearer scoped-token)
    API->>API: permission + RLS + audit + outbox   %% same path as a user
    API-->>W: result
    W-->>FD: output / error
    FD->>FD: record function_run; retry on failure
```

> Permission added: **`manageFunction`** (organisation scope) to author/run; a
> separate admin-scope guard for enabling functions that use the embedded isolate.

---

# 2. Case Timeline

**Mechanism:** the timeline is a **read-side projection**, not new write data. It
merges (a) entity date fields (case start/end, task start/end/due, observable
created, alert imported/sync) and (b) the **audit/activity events** (api-internals
Part 3) for that case's context, into one time-ordered stream.

- **API:** `POST /query` step `timeline` on a case → returns ordered
  `{at, kind, actor, entity, summary}` events. No new table needed; it's an
  aggregation over `audit` (filtered to the case's `AuditContext`) + entity dates.
- **Optional:** a `case_timeline_entry` table for **manual** analyst-added
  milestones ("contained at 14:03"), which merge into the same stream.
- **Edge cases:** merged cases (union the source cases' events, de-dup); RLS
  inherits from the case (timeline is only as visible as the case).

---

# 3. Metrics / KPIs

Distinct from custom fields: **numeric, defined per case-template, aggregatable**
for SLA dashboards (e.g. "time to containment", "# endpoints affected").

```sql
CREATE TABLE metric (                  -- definition
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    unit        TEXT,                    -- 'count' | 'seconds' | ...
    value_type  TEXT NOT NULL DEFAULT 'integer'   -- integer|float
);
CREATE TABLE case_metric (             -- value (one row per case×metric)
    case_id   BIGINT NOT NULL REFERENCES case_(id) ON DELETE CASCADE,
    metric_id BIGINT NOT NULL REFERENCES metric(id),
    value     DOUBLE PRECISION,
    PRIMARY KEY (case_id, metric_id)
);
```

- Case templates declare which metrics apply (+ defaults).
- **API:** part of the case update path (`PATCH /case/{id}` with `metrics`);
  read via `/query` + aggregations for dashboards.
- **Why separate from custom fields:** metrics are first-class numeric series the
  dashboard/aggregation engine groups and sums; modelling them as typed columns
  keeps aggregation fast and SLA reporting clean.

---

# 4. Comments

TH4 only had **task logs**. TH5 adds free-form **comments on cases and alerts**.

```sql
CREATE TABLE comment (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_type  TEXT NOT NULL,         -- 'case' | 'alert'
    subject_id    BIGINT NOT NULL,
    body          TEXT NOT NULL,         -- markdown
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ
);
CREATE INDEX ON comment (subject_type, subject_id);
```

- **API:** `POST/GET/PATCH/DELETE /{case|alert}/{id}/comment`.
- **Mechanism:** plain entity; mutations audited + streamed like any other, so
  comments appear live and in the timeline. RLS via the subject's visibility.
- **Edge cases:** when an **alert is promoted to a case**, its comments transfer
  to the case (TH5 behaviour) — copy `subject` from alert→case in the promotion
  service. Optional `@mention` → a notification trigger.

---

# 5. Alert pre-processing, observable dedup, similarity

**Pre-processing** = act on an alert *before* it becomes a case:
- Run Cortex analyzers on alert observables (reuse the Cortex connector, M7),
  attach `ReportTag` verdicts to the alert's observables.
- Add comments / TTPs (procedures) / metrics to the alert.
- "Alert preview": render what the case *would* look like (apply the case
  template) without committing.
- **Mechanism:** alerts already hold observables; allow the same analyzer-run +
  comment/TTP/metric writes on an alert that we allow on a case (extend those
  services to accept an alert subject).

**Observable dedup on import:** when ingesting an alert, upsert observables on
`(dataType, data)` within the alert (and reuse the shared `observable_data` dedup
table from the PoC) so repeated imports don't multiply observables.

**Similar alerts / cases:** computed by shared observable values.
- **Mechanism:** index observables by normalized `(dataType, data)`; "similar"
  = other cases/alerts sharing ≥1 observable value (weight by rarity).
- **Edge case (TH5 behaviour):** once an alert is **merged into a case**, the
  alert drops out of similarity and the **case** represents it — filter merged
  alerts out of the similarity source set.

---

# 6. Notifiers: Slack, MS Teams, generic HTTP (beyond Email/Webhook/Mattermost)

**Mechanism:** a `Notifier` plugin interface `send(event, config) -> Result`,
selected by `notifier.type`. Each is a thin formatter + HTTP client:

| Notifier | Mechanism |
|---|---|
| `slack` | POST to a Slack incoming-webhook / Web API with Block Kit payload |
| `msteams` | POST an Adaptive Card to a Teams webhook |
| `http` | arbitrary method/url/headers/body with templated event data |

- Config per notifier (webhook URL/token from the secret store), Jinja/Handlebars
  templating of the event payload.
- **Delivery via the outbox** (api-internals §3.3) with retry/backoff — so a
  flaky Slack endpoint never blocks the triggering transaction and never loses an
  event. This is just new `Notifier` implementations behind the existing trigger
  fan-out (roadmap M5).

---

# 7. Auth additions (SAML, session mgmt, password reset, directory sync)

Extend the chainable provider model (api-internals §3 / parity-spec §6):

- **SAML 2.0 SSO:** a `saml` provider — SP metadata, ACS endpoint, IdP signing
  cert, assertion → `AuthContext` (map NameID→login, attributes→org/profile).
  Sits in the provider chain next to OAuth2/OIDC.
- **Session management:** persist sessions (`session` table: id, user, created,
  last_seen, ip, user_agent, revoked_at); `GET /user/sessions` lists,
  `DELETE /user/session/{id}` revokes (drops the server-side session/refresh).
- **Self-service password reset:** `POST /auth/password/forgot` → emailed signed,
  single-use, expiring token → `POST /auth/password/reset`. Rate-limited; reuses
  the Emailer notifier; only for the `local` provider.
- **LDAP/AD directory sync:** scheduled job that reconciles users + group→profile
  mappings from the directory (create/lock users, adjust memberships) — distinct
  from LDAP *authentication*, which TH4 already has. Reuses the Functions/job
  scheduler (§1.4).

---

# 8. OpenAPI specification

**Mechanism:** generate the OpenAPI 3 document from the two things we already
have — the **route table** (paths/methods) and the **`PublicProperties` field
registry** (request/response field types, filters). The `/describe` endpoint
already serialises the registry; OpenAPI is a second renderer over the same
metadata. Serve at `/api/openapi.json` + a Swagger UI. Cheap, and it drives
client-SDK generation and the search-builder UI.

---

# 9. Case Reporting (Implement-later)

Template-driven report generation (TH5 gates this behind Platinum).

```sql
CREATE TABLE report_template (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id BIGINT NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    definition      JSONB NOT NULL          -- ordered widgets: text|image|table|list + data bindings
);
```

- **Widgets:** `text` (markdown w/ case-field interpolation), `table`/`list`
  (bound to tasks/observables/comments with a `/query` + field selection +
  max-rows), `image` (attachment/logo).
- **Render mechanism:** resolve each widget's data binding via the `/query`
  engine (so RLS applies), interpolate into the template, emit **Markdown** then
  **printable HTML** (HTML→PDF via the browser/print or a headless renderer).
- **Permission:** `manageCaseReportTemplate` to author; report generation gated
  by case visibility.
- **API:** `GET /case/{id}/report?template={id}&format=md|html`.

---

# New permissions introduced by these features

| Permission | Scope | For |
|---|---|---|
| `manageFunction` | organisation | author/run Functions |
| `manageMetric` | admin | metric definitions |
| `manageCaseReportTemplate` | organisation | report templates |
| (comments/timeline reuse `manageCase`/`manageAlert`) | organisation | — |

# How this maps to the roadmap

- **M10** — Functions (§1) + Case Reporting (§9).
- **M6** — Timeline (§2), Metrics (§3), Comments (§4), Alert pre-processing/dedup/
  similarity (§5).
- **M5** — Slack/Teams/HTTP notifiers (§6).
- **M9** — SAML/sessions/password-reset/dir-sync (§7).
- **M3** — OpenAPI (§8).

Everything here reuses the four load-bearing mechanisms from the existing docs
(service→audit→outbox, `/query`+registry, Share/RLS tenancy, the connector/worker
runner pattern) — which is why none of it lands on the serial critical path.
