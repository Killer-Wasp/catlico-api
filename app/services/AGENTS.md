# Service layer conventions

Scope: everything under `app/services/`. Complements the root `AGENTS.md`.

Business logic that is too big for a route handler and too cross-cutting for one CRUD
module. Services orchestrate CRUD; **CRUD never imports a service.** Keep that direction.

## Two kinds of module

### 1. Outbox consumers — post-commit fan-out

`app/main.py` registers exactly four at startup:

| Consumer | Module | Does |
|---|---|---|
| `notify_feed_consumer` | `outbox_events.py` | Creates a `UserNotification` per audit event |
| `notifier_delivery_consumer` | `notifier_delivery.py` | Rule matching → Webhook / Slack |
| `ws_broadcast_consumer` | `websocket_hub.py` | Live broadcast to connected clients |
| `plugin_event_consumer` | `plugin_dispatch.py` | Enqueues events for healthy plugin runners |

`outbox_events.build_event_envelope()` is the **stable event shape** every consumer
reads. `normalize_plugin_event_type()` maps `(object_type, action)` onto the past-tense
names plugins declare as triggers (`observable.created`). Both are shared vocabulary —
changing them changes what plugins receive.

Consumers run inside the drain transaction. `dispatch_pending_outbox` catches each
consumer's exceptions and marks the row delivered only if **all** succeeded; otherwise
the row is retried and **every consumer runs again**. Consumers must therefore be
**idempotent**. Keep them cheap: they hold the drain open.

### 2. Background pollers

Three `asyncio` tasks, all started and cancelled by the `lifespan` in `app/main.py`:

- **`_outbox_poller`** — drains the audit outbox every `OUTBOX_POLL_INTERVAL` (5s).
- **`_plugin_maintenance_poller`** (`plugin_maintenance.run_maintenance_sweep`) — reaps
  stuck runs, marks silent runners offline, rolls finished runs into `PluginRunDaily`.
- **`_plugin_push_poller`** (`plugin_dispatch.push_pending_deliveries`) — pushes queued
  plugin events to runners with retry/backoff.

Every poller wraps its body in `try/except Exception` and logs — **a poller must never
die on a transient error.** Re-raise only `asyncio.CancelledError`. Each sweep is a plain
async function taking a session, so tests can drive one iteration deterministically
without the loop.

## `plugin_dispatch.py` — the API→runner seam

Signs the **raw body** with `PLUGIN_RUNNER_SHARED_SECRET` (the same secret runners present
for inbound auth, used in both directions):
`x-catlico-signature: sha256=<hmac-sha256(shared_secret, body)>`. The runner recomputes and
compares in constant time. Events fan out to **every healthy runner** (selected on
status/heartbeat).

Plugin-actor events are suppressed so a plugin's own writes don't re-trigger it — an
easy infinite loop to reintroduce. Cron events use a deterministic `_schedule_event_id`
per (plugin, org, fire slot), which is what makes scheduling idempotent across restarts.

## Known state — check before you trust

- **`connector_operations.py`** validates and applies responder operations transactionally
  with audit rows, but responder job queueing and real responder connectors are incomplete.
- `plugin_audit.py` is **gone** (removed 2026-07-17). It wrote plugin admin actions (enable,
  config change, approval) straight to `Audit` with no outbox row, so nothing could ever read
  them — the audit viewers and `/api/v1/audit/` routes were removed by `fix/oos-slimdown.md`.
  Rebuild it alongside the long-term audit retention + export feature
  (`docs/FEATURE-ROADMAP.md` §3), not before: until there is a reader, the rows are write-only.
- `websocket_hub.py` is an **in-memory** hub — it does not survive multiple processes.
  It needs a shared backend before horizontal scaling.

## Style

- Services take an `AsyncSession` and do **not** commit inside a request — `get_session`
  owns that boundary. Poller-owned sessions (which run outside any request) do commit.
- Emit audit rows via `record_audit` for anything a user or admin would need to explain later.
- Keep secrets out of audit rows, envelopes, logs, and notifier payloads.
