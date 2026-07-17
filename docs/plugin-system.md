# The plugin system

How Catlico runs trusted **first-party** plugins without ever letting them near the database.

For the plugin **authoring** contract, see the [plugin SDK](https://github.com/Killer-Wasp/catlico-plugin-sdk).
For the **execution host** (a plain-subprocess runner — no sandbox, by design; see its
`docs/security.md` for why), see the [plugin runner](https://github.com/Killer-Wasp/catlico-plugin-runner).

> **Status.** This documents what the code does today. Sections marked *Not yet wired*
> describe deliberate gaps. The legacy connector worker (`catlico-konnect`) was **retired
> on 2026-07-10** — the plugin system is the only enrichment path.

## The trust model

Plugins are trusted first-party code; there is no sandbox around them. The boundaries below
are enforced at the API — via surfaces, tokens, and permissions — not by isolating plugin
code. Three rules, each enforced in code:

- **Plugins never touch the database.** They call the runtime HTTP API with a per-run token.
- **The runner holds no database credentials.** It reaches Catlico only over the internal HTTP API.
- **The web app never talks to a runner.** It talks only to this API.

This service is the permission gate and the audit writer for the whole subsystem.

## The three API surfaces

Separated by *who authenticates and how*. A caller on one surface can never reach
another surface's routes. Wiring lives in [`app/api/deps.py`](../app/api/deps.py).

| Surface | Prefix | Authenticated by | Principal |
|---|---|---|---|
| **Public** | `/api/v1/*` | User JWT **or** API key (`thp_…`), org via `X-Organisation-Id` | `AuthContext` |
| **Runner** | `/api/internal/plugin-runner/*` | The shared secret as `Authorization: Bearer <PLUGIN_RUNNER_SHARED_SECRET>`, plus `X-Runner-Id: <runner_id>` for identity/routing | `PluginRunnerPrincipal` |
| **Runtime** | `/api/internal/plugin-runtime/*` | Short-lived per-run token, minted at claim, dies at terminal status | `PluginRuntimePrincipal` |

(The legacy analyzer/responder surface — `ANALYZER_SHARED_SECRET`, `/api/internal/analyzer/*`,
`/api/internal/responder/*` — was deleted with the konnect retirement; these three are the
only internal surfaces.)

### Public — `/api/v1/plugins`

Permission gate: `read:connector` / `write:connector`; manual runs need `run:enrichment`.
The plugin system reuses the connector permission vocabulary.

| Method | Path | Notes |
|---|---|---|
| GET | `/plugins` | Catalog + per-org enable/config state |
| GET | `/plugins/{id}` | Single plugin DTO |
| GET | `/plugins/{id}/stats` | Usage stats (`window=7d\|30d\|90d`) |
| GET/POST | `/plugins/{id}/resources/{path}` | Proxy to the runner. **See caveat below.** |
| POST | `/plugins/{id}/run` | Dispatch a manual run (observable, case, or alert; `force` re-runs) |
| POST | `/plugins/{id}/enable` · `/disable` | Per-org enablement |
| POST | `/plugins/{id}/auto-run/enable` · `/disable` | Event-driven auto-run toggle |
| PUT | `/plugins/{id}/auto-apply` | Which low-risk proposal types auto-apply |
| PUT/GET | `/plugins/{id}/config` | Write-only secrets; GET returns settings + `has_secrets` |
| GET | `/plugins/{id}/config/status` | Server-owned per-parameter completeness |
| POST | `/plugins/{id}/config/test` | Validate required secrets are present |

> **Caveat — `resources` proxies to a route that doesn't exist.** The API forwards to the
> runner's `/internal/plugins/{id}/resources/{path}`, but the runner implements only four
> `/internal/*` routes and that is not one of them. These calls hit a missing route and
> return the proxy's 502 wrapper.

> **Caveat — `config/test` and `config/status` can disagree.** `config/test` recognises
> only params flagged `secret: true`, while the config-status and enable gate
> (`_is_secret_param`) recognise both `secret: true` and `type: "secret"`.

Also: `/api/v1/plugin-runs` (`GET ""`, `GET /{id}`, `POST /{id}/cancel`,
`POST /retry-failed` — counts only, does not re-dispatch — `POST /clear-finished`);
`/api/v1/plugin-runners` (**superadmin only**); and `/api/v1/proposed-actions`
(**JWT + org header, not API-key** — an API key must not approve its own plugin's proposals).

### Runner — `/api/internal/plugin-runner`

Every call authenticates with the shared secret as `Authorization: Bearer <PLUGIN_RUNNER_SHARED_SECRET>`,
which the API constant-time-compares against its own copy. `X-Runner-Id` names which runner is
calling — used purely as identity/routing and must match an existing `plugin_runner` row (else 404).

`POST /register` self-registers: authenticated by the shared secret alone, it **upserts** the
runner row (creating it if missing, marking it `healthy`) plus its plugin inventory, and accepts a
self-reported `base_url` (the URL the API pushes to). It returns only the runner summary — no
credential, no signing secret. Admins do not pre-provision runners or mint tokens.

`POST /heartbeat` · `GET /sync` (active plugins + org enablements, **never secrets**) ·
`POST /runs` (claim) · `POST /runs/{id}/accepted` · `/started` · `/skipped` ·
`GET /runs/{id}/config` (decrypted, only while `accepted`/`running`) · `POST /runs/{id}/result`.

### Runtime — `/api/internal/plugin-runtime`

Called by plugin code during a run. The token carries the run's org scope, entity context,
and the plugin's manifest permissions. Every route checks a permission and re-validates
the target is in the run's org / case share.

- **Reads:** `GET /cases/{id}`, `/alerts/{id}`, `/observables/{id}`
- **Evidence (append-only):** `POST /results` (idempotent on `(run, fingerprint)`),
  `POST /observables/{id}/enrichments`, `POST /files` + `GET /files/{ref}` (run-scoped
  blobs; download limited to the triggering file observable), `POST /progress`
- **Direct low-risk writes:** `POST /cases/{id}/comments`,
  `POST /cases/{id}/tasks/{tid}/logs`, `PATCH /observables/{id}`
- **Proposed, not applied:** `PATCH /cases/{id}`, `POST /cases/{id}/tasks`,
  `POST /cases/{id}/tags` — these return **202** with a proposal id

## Data model

Defined in [`app/models/plugin_runner.py`](../app/models/plugin_runner.py); introduced by the
`r4b7d9e1f3a5_add_plugin_runner.py` migration, with a later migration dropping the enrollment
columns (`enrollment_state`, `credential_hash`, `enrollment_token_hash`,
`enrollment_token_expires_at`, `push_signing_secret_encrypted`).

| Model | Purpose |
|---|---|
| `PluginRunner` | A self-registered runner: `base_url`, status, heartbeat/health |
| `PluginDefinition` | Global catalog row; manifest + `active_version_id` |
| `PluginVersion` | One immutable installed version, with source/build provenance |
| `RunnerPluginInstallation` | PK `(runner_id, plugin_version_id)` — which runner hosts which version. Replaces the old single-runner model, enabling multi-runner |
| `OrgPlugin` | Per-org enablement, `auto_run_enabled`, `schedule_override`, `suspended_reason`, `auto_apply_actions` |
| `PluginConfig` | Per-org settings + `secrets_encrypted` |
| `PluginRun` | One execution attempt: status, error/log, progress, run-token hash |
| `PluginEventDelivery` | API→runner push queue and retry state |
| `PluginResult` | Append-only evidence for any entity; verdict/confidence, `render_mode`, attachments, `fingerprint`, `expires_at` |
| `PluginRunFile` | Run-scoped uploaded blob for result attachments |
| `PluginProposedAction` | A plugin-requested canonical mutation awaiting approval |
| `PluginRunDaily` | Daily rollup that survives run pruning; feeds stats |

**Claim arbiter.** `PluginRun` is unique on `(event_id, plugin_id)`
(`uq_plugin_run_event_plugin`). When several runners host the same plugin and all receive
the same event push, the **first** to `POST /runs` wins; the rest get **409** and drop the
work. That is the multi-runner de-duplication mechanism: one run per event per plugin.

> *Not yet wired:* nothing increments `PluginRun.attempt`, and no retry path re-dispatches
> onto an existing run row. `POST /retry-failed` only counts. (The `attempts` counter that
> *is* incremented belongs to `PluginEventDelivery` — a different model, for push retries.)

`PluginResult` and `PluginRunFile` reference `plugin_run` with **`ON DELETE SET NULL`**, so
evidence and attachments outlive their run when runs are pruned.

## Event dispatch

[`app/services/plugin_dispatch.py`](../app/services/plugin_dispatch.py). Two stages,
deliberately split so database work and network I/O never share a transaction.

**1. `plugin_event_consumer`** runs inside the outbox drain. For each committed audit event:

- **Plugin-actor suppression.** If `actor` starts with `plugin:`, return immediately.
  Plugin-caused events are never redispatched — this is the loop guard.
- **Event-type normalization.** The audit envelope's `(object_type, action)` is normalized
  to past tense (`case.create` → `case.created`) by `normalize_plugin_event_type`. Manifest
  `triggers` match against the normalized form; a mismatch means nothing fires.
- If an installed plugin's manifest triggers include the event type, enqueue one
  `PluginEventDelivery` per **healthy** runner (selected on status/heartbeat), idempotent on
  `(event_id, runner_id)`.

**2. `push_pending_deliveries`** (its own poller) POSTs each due envelope to the runner's
`/internal/events`, signed with the shared secret:

```
x-catlico-signature: sha256=<hmac-sha256(PLUGIN_RUNNER_SHARED_SECRET, raw_body)>
```

The runner recomputes the HMAC with the same secret to verify.

Failures reschedule with exponential backoff (`PLUGIN_PUSH_BACKOFF_*`) up to
`PLUGIN_PUSH_MAX_AGE_SECONDS`, after which the delivery is marked `expired`.

The runner then **claims** the run via `POST /api/internal/plugin-runner/runs`, where the API
enforces version-active, installation, trigger, org enablement + auto-run, the
`(event_id, plugin_id)` claim, plus **skip checks**: freshness (`result_ttl_seconds` against
a live `PluginResult`), **TLP/PAP ceilings** (`max_tlp` / `max_pap`), and a global
**concurrency cap** (`max_concurrent_runs`, returns 429 to defer). Skipped runs are recorded
terminal without minting a token.

**Cron.** `schedule_due_events` (run in the maintenance sweep, via `croniter`) emits
`schedule.fired` events for plugins whose manifest lists that trigger and whose org has
auto-run on. Firing is **API-side only**, with a deterministic slot id
(`schedule:<plugin>:<org>:<epoch>`), so N runners cannot fire N times. It uses the
most-recent slot only — at most one catch-up after an outage.

**Manual runs ride the same path.** `POST /plugins/{id}/run` no longer inserts a `queued` run
nothing executes (that old scaffold is gone — see `create_manual_plugin_run`): it synthesizes a
manual envelope (`event_id = manual:<org>:<plugin>:<entity_type>:<entity_id>`, deterministic so
repeat clicks dedup; `force=true` salts it with a time component so a real re-run dispatches) and
enqueues a `PluginEventDelivery` per healthy runner. The runner claims through the normal path;
`create_run` reads the stored envelope's server-side `manual` flag and relaxes trigger-match,
auto-run, and freshness-cache checks — analyst intent — while keeping the TLP/PAP ceilings and
the concurrency cap.

## Proposed actions

[`app/crud/plugin_proposed_action.py`](../app/crud/plugin_proposed_action.py).

Evidence (`PluginResult`) is append-only and safe, so plugins write it freely. **Canonical
entity edits** — patch a case, add a tag, create a task — are not applied by plugin code.
They become `PluginProposedAction` rows an analyst approves, so audit, activity, and outbox
behaviour match a human edit.

`action_type` vocabulary: `add_tag`, `create_task`, `append_task_log`,
`add_related_observable`, `change_severity_status`, `patch_case_description`,
`execute_responder_action`.

All of these apply on approval **except `execute_responder_action`**, which is deliberately
excluded — there is no post-approval path from a proposed action to a responder. (The
connector-job pipeline in `catlico-konnect` that once executed responders was retired
2026-07-10; nothing has replaced it yet.)

> **Approving an `execute_responder_action` returns HTTP 200 with `status: "failed"`** and an
> informative `decision_reason` — not a 422. `decide()` catches the internal error, marks the
> row failed, and returns it. It never falls through to another action. The approve route's
> only error codes are 403, 404, and 409 (already decided).

**Producers vs. appliers.** Only three proposal types have a producer today:
`patch_case_description`, `create_task`, `add_tag`. `add_related_observable` *is* wired to
apply on approval (org-scoped, idempotent, and it deliberately skips enrichment
re-triggering to avoid plugin→observable→enrichment loops) but no runtime route or SDK
method creates it — its apply path is dormant. `append_task_log` and
`change_severity_status` are likewise applier-only. Nothing pins the applicable set to the
runtime's proposing routes, so a new `propose_*` route with an unlisted type would fail on
approval with no test catching it.

**Approval gating.** The approving user must hold the same permission the equivalent manual
action needs: `write:case` for a case patch, `write:task` for a task, `write:observable` for
an observable tag or link.

**Actor string.** An applied action records both parties:
`plugin:<id>@<ver> approved-by user:<uid>` (or `… auto-applied` under org policy).

**Org auto-apply policy.** An org may opt specific low-risk types (`add_tag`, `create_task`,
`append_task_log`, `add_related_observable`) into auto-apply via `PUT /plugins/{id}/auto-apply`.
Those proposals are born `applied`; everything else is analyst-gated regardless of policy.

**Terminal-status guarantee.** Both `decide()` and the auto-apply path run `apply()` inside a
savepoint (`begin_nested`). A genuine `IntegrityError` rolls back to the savepoint, not the
whole transaction, and the row becomes terminal `failed` with a generic `decision_reason`
(raw driver text is logged server-side, never leaked). A validation error marks `failed` —
or, for auto-apply, leaves the row `proposed` for later approval. A proposal never gets stuck
in `proposed` and never surfaces as a 500.

## Maintenance sweep

[`app/services/plugin_maintenance.py`](../app/services/plugin_maintenance.py). One pass every
`PLUGIN_MAINTENANCE_INTERVAL_SECONDS` (30s). Each step is a plain async function, so tests can
drive it with an injected `now`.

| Step | Behaviour | Settings |
|---|---|---|
| `reap_stuck_runs` | Fail active runs past `start/create + timeout + grace`; invalidate token, set `error_kind="timeout"` | `PLUGIN_RUN_DEFAULT_TIMEOUT_SECONDS`=60, `PLUGIN_RUN_REAP_GRACE_SECONDS`=60 |
| `detect_offline_runners` | Mark runners `offline` after N missed heartbeats | `PLUGIN_HEARTBEAT_INTERVAL_SECONDS`=30 × `PLUGIN_RUNNER_OFFLINE_MISSED_HEARTBEATS`=3 |
| `rollup_terminal_runs` | Fold terminal runs into `PluginRunDaily`, mark `rolled_up`. Runs **before** pruning | — |
| `prune_old_runs` | Delete terminal, rolled-up runs past the window | `PLUGIN_RUN_RETENTION_DAYS`=30 |
| `prune_old_deliveries` | Delete delivered/expired deliveries past the window | `PLUGIN_DELIVERY_RETENTION_DAYS`=7 |
| `prune_superseded_results` | Delete superseded results past the window (latest is never deleted) | `PLUGIN_RESULT_RETENTION_DAYS`=90 |
| `schedule_due_events` | Emit due `schedule.fired` events | — |

## Config and secrets

Settings and secrets are **write-only from the public API** (`PUT /plugins/{id}/config`).
`GET` returns settings plus a `has_secrets` boolean — secrets are never returned in the clear.
Secrets are Fernet-encrypted at rest and **merged** on write (absent key keeps, string
replaces, `null` deletes), so a single-field update cannot drop the rest. The runner receives
decrypted config only per-run, and only while the run is `accepted`/`running`.

**Audit records key names only.** `plugin_audit.summarize_config_change` records non-secret
keys as `from`→`to` and secret keys as *just the key name* — never a value. Admin actions
(enable/disable/auto-run/config/approve/reject) are written straight to the `Audit` table,
bypassing the outbox fan-out, because plugin-system object types must never be dispatched to
runners.

**`config_status`** is computed server-side so enable-gating cannot drift from a client
recomputation. Precedence: a param sourced from `environment` is *locked and configured*
(its value comes from the runner's environment); otherwise an org value or a schema default
satisfies it; a required param with neither is `missing`.

> **Contributor trap.** Schema defaults are read as **`defaultValue`, falling back to
> `default`**. Use `defaultValue` when adding config-form logic.

## Invariants — do not regress

- **One run per `(event_id, plugin_id)`.** The DB unique constraint is the multi-runner claim arbiter.
- **Plugin-actor events are never dispatched.** Loop prevention.
- **Secrets are write-only from the public API**, redacted everywhere; config audit logs key
  names only; `environment`-sourced params are reported locked.
- **Run tokens die at terminal status.** `_invalidate_runtime_token` clears the hash on
  skip/result/reap; the runtime auth dependency rejects tokens whose run isn't
  `accepted`/`running`. Late results are logged, never applied.
- **Evidence is append-only.** Canonical edits go through `PluginProposedAction` unless org
  policy auto-applies a low-risk type.
- **The web app never talks to a runner; plugins never touch the database.**

## Tests

| File | Covers |
|---|---|
| `test_plugin_runner_models.py` | Model/migration shape, constraints |
| `test_api_plugins_public.py` | Public catalog/config/enable routes |
| `test_api_plugin_runners.py` · `_public.py` | Runner admin + self-registration |
| `test_api_plugin_run_claim.py` | Claim, skip checks, concurrency cap |
| `test_api_plugin_runtime.py` | Runtime reads/results/files/progress, token scoping |
| `test_api_proposed_actions.py` | Proposal apply/approve/reject, auto-apply |
| `test_plugin_dispatch.py` | Outbox consumer, push/backoff, cron scheduling |
| `test_plugin_maintenance.py` | Reaper, offline detection, rollup, retention |
| `test_api_plugin_stats.py` | Usage-stats aggregation |
| `test_api_plugin_admin_audit.py` | Admin-action audit incl. secret-key redaction |
