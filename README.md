# Catlico API

FastAPI service (`catlico-api`) — the **control plane** for Catlico and the **only
service with database credentials**. Everything that touches the DB goes through
here: the web app, API keys, the legacy connector worker (`catlico-konnect`), and
the new plugin system.

For the plugin subsystem specifically, this service is the **permission gate and
audit writer**. Plugins never touch the database; the plugin **runner** has no DB
credentials and reaches Catlico only over the internal HTTP API; the **web app
never talks to a runner** — it talks only to this API.

> **Scope note.** This README documents what the code does today. The full target
> architecture lives in
> [`docs/catlico-plugin-runner-replacement-plan.md`](../docs/catlico-plugin-runner-replacement-plan.md).
> That plan describes **intent, not current state** — large parts are unbuilt.
> `../todo.md` tracks the code-vs-plan drift. When in doubt, the code wins.

The legacy connector/analyzer system (`catlico-konnect`, ~35 connectors) is **still
the production enrichment path** and runs alongside the plugin system. It is not
retired.

---

## Running locally

```bash
make dev
```

`make dev` (see [`Makefile`](Makefile)):

1. `docker compose up -d db seaweedfs` — Postgres 16 on `:5432` and a SeaweedFS S3
   gateway on `:8333` (see [`docker-compose.yml`](docker-compose.yml)). Postgres is
   the only supported engine; dev, test, and prod all run on it.
2. Exports local Postgres defaults (`catlico`/`catlico`/`catlico`) if `.env`
   doesn't set them, then runs `uv run uvicorn app.main:app --reload` on
   **`127.0.0.1:8000`**.

On startup the lifespan (`app/main.py`):

- Validates `SECRET_ENCRYPTION_KEY` is a valid Fernet key (hard failure otherwise).
- When `ENVIRONMENT=local`, applies **Alembic migrations to head** automatically
  (`run_migrations`), so a fresh checkout needs no manual `make migrate`.
- Seeds built-in roles, observable types, the default superadmin, and local demo
  data (`app/core/db.py::init_db`).
- Registers outbox consumers and starts four background loops (see below).

Cold start takes ~15–20s (migrations + seeding). Confirm readiness with:

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/docs   # expect 200
```

The outbox poller's retry logs (e.g. `user_notification` FK violations from stale
seed data) are **harmless background noise**, not a startup failure — check `/docs`
rather than tailing logs.

**Seeded dev login:** `admin@example.com` / `changeme` (from `DEFAULT_ADMIN_EMAIL`
/ `DEFAULT_ADMIN_PASSWORD` in `.env`; defaults in `app/core/configs.py`).

**Interactive docs:** `/docs`, `/redoc`, `/openapi.json` (disabled in `production`).

### Background loops (started in `app/main.py` lifespan)

| Loop | Interval setting | Purpose |
|---|---|---|
| `_outbox_poller` | `OUTBOX_POLL_INTERVAL` = 5s | Drains the audit outbox, fanning each row to registered consumers. |
| `run_function_poller` | — | Function-runner (non-plugin). |
| `_plugin_maintenance_poller` | `PLUGIN_MAINTENANCE_INTERVAL_SECONDS` = 30s | Reaper, offline detection, rollups, retention pruning, cron scheduling. |
| `_plugin_push_poller` | `PLUGIN_PUSH_INTERVAL_SECONDS` = 5s | Pushes queued event deliveries to runners. |

Registered outbox consumers: `notify_feed_consumer`, `notifier_delivery_consumer`,
`ws_broadcast_consumer`, and **`plugin_event_consumer`** (`app/services/plugin_dispatch.py`).

---

## The three API surfaces

The service exposes three distinct surfaces, separated by **who authenticates and
how**. The rule: a caller on one surface can never reach another surface's routes.

| Surface | Prefix | Authenticated by | Principal |
|---|---|---|---|
| **Public** | `/api/v1/*` | User JWT **or** API key (`thp_…`), org from `X-Organisation-Id` / path; some routes superadmin-only | `AuthContext` |
| **Runner** | `/api/internal/plugin-runner/*` | Per-runner machine credential (`Bearer cpr_…`); `/register` instead presents a one-time enrollment token | `PluginRunnerPrincipal` |
| **Runtime** | `/api/internal/plugin-runtime/*` | Short-lived per-run token (`Bearer`), minted when a run is claimed, dies at terminal status | `PluginRuntimePrincipal` |

Auth wiring is in [`app/api/deps.py`](app/api/deps.py). The legacy analyzer/responder
worker (`catlico-konnect`) authenticates separately via `ANALYZER_SHARED_SECRET`
on `/api/internal/analyzer/*` and `/api/internal/responder/*` (out of scope here).

### Public — `/api/v1/plugins` ([`plugins.py`](app/api/v1/routes/plugins.py))

Permission gate: `read:connector` / `write:connector`; manual runs need
`run:enrichment`. The plugin system reuses the connector permission vocabulary.

| Method | Path | Notes |
|---|---|---|
| GET | `/plugins` | Catalog + per-org enable/config state. |
| GET | `/plugins/{id}` | Single plugin DTO. |
| GET | `/plugins/{id}/stats` | Usage stats (`window=7d\|30d\|90d`). |
| GET/POST | `/plugins/{id}/resources/{path}` | Proxies to the hosting runner's `/internal/plugins/…`. |
| POST | `/plugins/{id}/run` | Queue a manual run for an entity (`run:enrichment`; observables only). |
| POST | `/plugins/{id}/enable` · `/disable` | Per-org enablement. |
| POST | `/plugins/{id}/auto-run/enable` · `/disable` | Event-driven auto-run toggle. |
| PUT | `/plugins/{id}/auto-apply` | Set which low-risk proposal types auto-apply. |
| PUT/GET | `/plugins/{id}/config` | Write-only secrets; GET returns settings + `has_secrets` only. |
| GET | `/plugins/{id}/config/status` | Server-owned per-parameter completeness. |
| POST | `/plugins/{id}/config/test` | Validate required secrets are present. **Note:** this route only recognises params flagged `secret: true`, not `type: "secret"`, so it can diverge from the config-status/enable gate (`_is_secret_param`), which recognises both. |

`/api/v1/plugin-runs` ([`plugins.py`](app/api/v1/routes/plugins.py), `runs_router`):
`GET ""`, `GET /{id}`, `POST /{id}/cancel`, `POST /retry-failed` (counts only —
does not re-dispatch), `POST /clear-finished`.

`/api/v1/plugin-runners` ([`plugin_runners.py`](app/api/v1/routes/plugin_runners.py)) —
**superadmin only**: `GET ""`, `POST ""` (mints an enrollment token), `GET /{id}`,
`GET /{id}/stats`, `POST /{id}/health-check`, `POST /{id}/sync`.

`/api/v1/proposed-actions` ([`proposed_actions.py`](app/api/v1/routes/proposed_actions.py)) —
JWT + org header (**not** API-key): `GET ""`, `POST /{id}/approve`, `POST /{id}/reject`.

### Runner — `/api/internal/plugin-runner` ([`plugin_runner.py`](app/api/internal/routes/plugin_runner.py))

Called by an enrolled runner machine. `POST /register` exchanges a one-time
enrollment token for a machine credential (`cpr_…`) **and** a push-signing secret
(`cps_…`, stored Fernet-encrypted so the API can sign event pushes). Everything
else presents the machine credential.

`POST /register` · `POST /heartbeat` · `GET /sync` (active plugins + org
enablements, **never secrets**) · `POST /runs` (claim, see dispatch) ·
`POST /runs/{id}/accepted` · `/started` · `/skipped` · `GET /runs/{id}/config`
(decrypted settings + secrets, only while `accepted`/`running`) ·
`POST /runs/{id}/result`.

### Runtime — `/api/internal/plugin-runtime` ([`plugin_runtime.py`](app/api/internal/routes/plugin_runtime.py))

Called by plugin code during a run, holding that run's token. The token carries the
run's org scope, entity context, and the plugin's manifest permissions; every route
checks a permission and re-validates the target is in the run's org/case share.

- **Reads:** `GET /cases/{id}`, `/alerts/{id}`, `/observables/{id}`.
- **Evidence (append-only):** `POST /results` (idempotent on
  `(run, fingerprint)`), `POST /observables/{id}/enrichments` (writes a
  `PluginResult`), `POST /files` + `GET /files/{ref}` (run-scoped blobs; download
  is limited to the triggering file observable), `POST /progress`.
- **Direct low-risk writes:** `POST /cases/{id}/comments`,
  `POST /cases/{id}/tasks/{tid}/logs`, `PATCH /observables/{id}`.
- **Proposed (not applied directly):** `PATCH /cases/{id}` →
  `patch_case_description`, `POST /cases/{id}/tasks` → `create_task`,
  `POST /cases/{id}/tags` → `add_tag`. These return **202** with a proposal id.

---

## Plugin data model ([`app/models/plugin_runner.py`](app/models/plugin_runner.py))

| Model | Table | Purpose |
|---|---|---|
| `PluginRunner` | `plugin_runner` | A registered runner: enrollment state, `credential_hash`, encrypted `push_signing_secret`, heartbeat/health. |
| `PluginDefinition` | `plugin_definition` | Global catalog row; holds the manifest and `active_version_id`. |
| `PluginVersion` | `plugin_version` | One immutable installed version (`"{plugin}@{ver}"`), source/build provenance. |
| `RunnerPluginInstallation` | `runner_plugin_installation` | PK `(runner_id, plugin_version_id)` — which runner hosts which version. **Supersedes the old `PluginDefinition.runner_id` FK**, enabling multi-runner. |
| `OrgPlugin` | `org_plugin` | Per-org enablement, `auto_run_enabled`, `schedule_override`, `suspended_reason`, `auto_apply_actions`. |
| `PluginConfig` | `plugin_config` | Per-org settings + `secrets_encrypted` (write-only from the public API). |
| `PluginRun` | `plugin_run` | One execution attempt; status, attempt, error/log, progress, run-token hash. |
| `PluginEventDelivery` | `plugin_event_delivery` | API→runner push queue and retry state. |
| `PluginResult` | `plugin_result` | Append-only evidence for any entity (`entity_type`/`entity_id`), verdict/confidence, `render_mode`, attachments, `fingerprint`, `expires_at`. |
| `PluginRunFile` | `plugin_run_file` | Run-scoped uploaded blob for result attachments. |
| `PluginProposedAction` | `plugin_proposed_action` | A plugin-requested canonical mutation awaiting policy/approval. |
| `PluginRunDaily` | `plugin_run_daily` | Daily run rollup that survives run pruning; feeds stats. |

**Claim arbiter.** `PluginRun` has a unique constraint on `(event_id, plugin_id)`
(`uq_plugin_run_event_plugin`). When several runners host the same plugin and all
receive the same event push, the **first** to `POST /runs` wins; the rest get
**409** and drop the work. This is the multi-runner de-duplication mechanism — one
run per event per plugin, and retries reuse the row (`attempt++`).

`PluginResult` / `PluginRunFile` reference `plugin_run` with **`ON DELETE SET
NULL`**, so evidence and attachments outlive their run when runs are pruned.

The single Alembic migration for all of this is
`alembic/versions/r4b7d9e1f3a5_add_plugin_runner.py`.

---

## Event dispatch ([`app/services/plugin_dispatch.py`](app/services/plugin_dispatch.py))

Two stages, deliberately split so DB work and network I/O never share a transaction:

1. **`plugin_event_consumer`** runs inside the outbox drain. For each committed
   audit event:
   - **Plugin-actor suppression:** if `actor` starts with `plugin:`, return
     immediately — plugin-caused events are never redispatched (loop prevention).
   - **Event-type normalization:** the audit envelope's `(object_type, action)` is
     normalized to **past tense** (`case.create` → `case.created`) by
     `normalize_plugin_event_type` (`app/services/outbox_events.py`). Manifest
     `triggers` are matched against this normalized form — mismatch means nothing
     fires.
   - If any installed plugin's manifest `triggers` include the event type, enqueue
     one **`PluginEventDelivery`** (pending) per **healthy, enrolled** runner
     (idempotent on `(event_id, runner_id)`).
2. **`push_pending_deliveries`** (its own poller) POSTs each due delivery's envelope
   to the runner's `/internal/events`, signed `x-catlico-signature: sha256=<hmac>`
   using the runner's decrypted push-signing secret. Failures reschedule with
   exponential backoff (`PLUGIN_PUSH_BACKOFF_*`) up to `PLUGIN_PUSH_MAX_AGE_SECONDS`,
   after which the delivery is marked `expired`.

The runner receives the envelope, decides locally whether an enabled plugin wants
it, then **claims** the run via `POST /api/internal/plugin-runner/runs`, where the
API enforces version-active, installation, trigger, org-enablement + auto-run, the
`(event_id, plugin_id)` claim, plus **skip checks** — freshness (`result_ttl_seconds`
vs a live `PluginResult`), **TLP/PAP ceilings** (`max_tlp`/`max_pap`), and a global
**concurrency cap** (`max_concurrent_runs`, returns 429 to defer). Skipped runs are
recorded terminal without minting a token.

**Cron.** `schedule_due_events` (run in the maintenance sweep, using `croniter`)
emits `schedule.fired` events for plugins whose manifest lists that trigger and
whose org has auto-run on. Firing is **API-side only** with a deterministic slot id
(`schedule:<plugin>:<org>:<epoch>`), so N runners can't fire N times, and it uses
the most-recent slot only (at-most-one catch-up after an outage).

> **Note on manual runs.** `POST /plugins/{id}/run` inserts a `queued` `PluginRun`
> directly. The runner-facing push path is driven by `PluginEventDelivery` (from the
> outbox), not by manually-inserted run rows — there is no observed code path that
> hands a manually-created run to a runner. Treat manual runs as create-only for now.

---

## Proposed actions ([`app/crud/plugin_proposed_action.py`](app/crud/plugin_proposed_action.py))

Evidence (`PluginResult`) is append-only and safe, so plugins write it freely.
**Canonical entity edits** (patch a case, add a tag, create a task) are not applied
by plugin code — they become `PluginProposedAction` rows that an analyst approves,
so audit/activity/outbox behaviour matches a human edit.

**`action_type` vocabulary** (`ACTION_TYPES`):
`add_tag`, `create_task`, `append_task_log`, `add_related_observable`,
`change_severity_status`, `patch_case_description`, `execute_responder_action`.

Which types actually **apply** on approval (`_APPLICABLE`): all of the above
**except `execute_responder_action`**. `execute_responder_action` is deliberately
excluded — there is **no post-approval execution path** from a proposed action to a
responder (responders run through the separate connector-job pipeline in
`catlico-konnect`). Approving one **fails cleanly** with an informative
`decision_reason` and a 422; it never falls through to another action.

> **Producers vs. appliers — reality check.** Only **three** proposal types have a
> producer today: `patch_case_description`, `create_task`, and `add_tag` (the three
> proposing runtime routes). `add_related_observable` **is** wired to apply on
> approval (org-scoped, idempotent, actor as below, and it deliberately skips
> enrichment re-triggering to avoid plugin→observable→enrichment loops) but **no
> runtime route or SDK method creates it** — its apply path is dormant.
> `append_task_log` and `change_severity_status` are likewise applier-only with no
> producer. Nothing pins `_APPLICABLE` to the runtime's proposing routes, so a new
> `propose_*` route with an unlisted type would 422 on approval with no test
> catching it.

**Approval gating.** The approving user must hold the same permission the equivalent
manual action needs (`approve_permission`): e.g. `write:case` for a case patch,
`write:task` for a task, `write:observable` for an observable tag/link.

**Actor string.** An applied action records both parties:
`plugin:<id>@<ver> approved-by user:<uid>` (or `… auto-applied` under org policy).

**Org auto-apply policy.** An org may opt specific **low-risk** types
(`LOW_RISK_ACTIONS` = `add_tag`, `create_task`, `append_task_log`,
`add_related_observable`) into auto-apply via `PUT /plugins/{id}/auto-apply`. Those
proposals are born `applied`; everything else is analyst-gated regardless of policy.

**Terminal-status guarantee.** Both `decide()` and the auto-apply path run `apply()`
inside a **savepoint** (`begin_nested`). A genuine `IntegrityError` rolls back to the
savepoint (not the whole transaction) and the row is set to a terminal `failed` with
a generic `decision_reason` (raw driver text is logged server-side, never leaked). A
validation `HTTPException` marks `failed` (or, for auto-apply, falls back to leaving
the row `proposed` for later approval). The result: a proposal never gets stuck in
`proposed` and never surfaces as a 500. `add_related_observable` additionally handles
its own dedup TOCTOU race in an inner savepoint (idempotent create/link).

---

## Maintenance sweep ([`app/services/plugin_maintenance.py`](app/services/plugin_maintenance.py))

One pass runs every `PLUGIN_MAINTENANCE_INTERVAL_SECONDS` (30s), each step a plain
async function so tests can drive it with an injected `now`:

| Step | Behaviour | Settings |
|---|---|---|
| `reap_stuck_runs` | Fail active runs past `start/create + timeout + grace`; invalidate token, set `error_kind="timeout"`. Timeout is the manifest's `timeout_seconds` or the default. | `PLUGIN_RUN_DEFAULT_TIMEOUT_SECONDS`=60, `PLUGIN_RUN_REAP_GRACE_SECONDS`=60 |
| `detect_offline_runners` | Mark enrolled runners `offline` after N missed heartbeats. | `PLUGIN_HEARTBEAT_INTERVAL_SECONDS`=30 × `PLUGIN_RUNNER_OFFLINE_MISSED_HEARTBEATS`=3 |
| `rollup_terminal_runs` | Fold not-yet-rolled terminal runs into `PluginRunDaily`, mark `rolled_up` (counted once). Runs **before** pruning. | — |
| `prune_old_runs` | Delete terminal, rolled-up runs past the window. | `PLUGIN_RUN_RETENTION_DAYS`=30 |
| `prune_old_deliveries` | Delete delivered/expired deliveries past the window. | `PLUGIN_DELIVERY_RETENTION_DAYS`=7 |
| `prune_superseded_results` | Delete results past the window that a newer result supersedes (latest is never deleted). | `PLUGIN_RESULT_RETENTION_DAYS`=90 |
| `schedule_due_events` | Emit due `schedule.fired` events (see dispatch). | — |

---

## Config & secrets

Settings and secrets are **write-only from the public API**
(`PUT /plugins/{id}/config`). `GET /plugins/{id}/config` returns settings plus a
`has_secrets` boolean — secrets are **never returned in the clear**. Secrets are
Fernet-encrypted at rest (`secrets_encrypted`) and merged on write (absent key
keeps, string replaces, `null` deletes), so a single-field update can't drop the
rest. The runner receives decrypted config only per-run via
`GET /plugin-runner/runs/{id}/config`, and only while the run is `accepted`/`running`.

**Audit records key names only.** `plugin_audit.summarize_config_change`
(`app/services/plugin_audit.py`) records non-secret keys as `from`→`to` and secret
keys as **just the key name** — never a value. Admin actions (enable/disable/auto-run/
config/approve/reject) are written straight to the `Audit` table, bypassing the
outbox fan-out (plugin-system object types must never be dispatched to runners).

**`config_status` DTO.** `GET /plugins/{id}/config/status` (and the `config_complete`
field on the plugin DTO) is computed **server-side** so enable-gating can't drift
from a client recomputation. Precedence: a param sourced from `environment` is
**locked and configured** (its value comes from the runner's environment, immutable
via config), otherwise an org value or a schema default satisfies it; a required
param with neither is `missing`.

> **Contributor trap.** Schema defaults are read as **`defaultValue`, falling back
> to `default`** — see `_param_config_status` in `plugins.py:54`. Use `defaultValue`
> when adding config-form logic.

---

## Key invariants (do not regress)

Verified against current code:

- **One run per `(event_id, plugin_id)`** — the DB unique constraint is the
  multi-runner claim arbiter; retries reuse the row (`attempt++`).
- **Plugin-actor events are never dispatched** — `plugin_event_consumer` drops any
  event whose `actor` starts with `plugin:` (loop prevention).
- **Secrets are write-only from the public API**, redacted everywhere; config audit
  logs key names only; `environment`-sourced params are reported locked.
- **Run tokens die at terminal status** — `_invalidate_runtime_token` clears the
  hash on skip/result/reap; the runtime auth dep rejects tokens whose run isn't
  `accepted`/`running`. Late results are logged, never applied.
- **Evidence is append-only** (`PluginResult`); canonical edits go through
  `PluginProposedAction` unless org policy auto-applies a low-risk type.
- **The web app never talks to a runner; the API never lets plugins touch the DB** —
  runner/runtime traffic is confined to the two internal surfaces; the runner holds
  no DB credentials.

---

## Testing

```bash
make test        # uv run pytest — spins a throwaway Postgres via testcontainers (needs Docker)
```

Notable plugin test files (`tests/`):

| File | Covers |
|---|---|
| `test_plugin_runner_models.py` | Model/migration shape, constraints. |
| `test_api_plugins_public.py` | Public catalog/config/enable routes. |
| `test_api_plugin_runners.py` · `_public.py` | Runner admin + enrollment. |
| `test_api_plugin_run_claim.py` | `(event_id, plugin_id)` claim, skip checks, concurrency cap. |
| `test_api_plugin_runtime.py` | Runtime reads/results/files/progress, run-token scoping. |
| `test_api_proposed_actions.py` | Proposal apply/approve/reject, auto-apply, terminal-status handling. |
| `test_plugin_dispatch.py` | Outbox consumer, push/backoff, cron scheduling. |
| `test_plugin_maintenance.py` | Reaper, offline detection, rollup, retention pruning. |
| `test_api_plugin_stats.py` | Usage-stats aggregation. |
| `test_api_plugin_admin_audit.py` | Admin-action audit incl. secret-key redaction. |

---

## Sibling repos

- `catlico-plugin-runner/` — the runner service (HTTP server, enrollment client,
  dispatch engine, subprocess + container sandboxes, install pipeline). No DB access.
- `catlico-plugin-sdk/` — the plugin author SDK (base class, typed errors, runtime
  API client, `FakeContext`/`FakeCatlicoApi` test kit, `catlico-plugin` CLI).
- `catlico-konnect/` — the **still-active** legacy connector/analyzer worker.
