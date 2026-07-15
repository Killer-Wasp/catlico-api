# API layer conventions

Scope: everything under `app/api/`. Complements the root `AGENTS.md`.

Two router trees, mounted in `app/main.py`:

| Tree | Prefix | Audience |
|---|---|---|
| `v1/` | `/api/v1` | Browsers and API-key clients |
| `internal/` | `/api/internal` | Machine principals: analyzer workers, responders, plugin runners, plugin runs |

`deps.py` holds every dependency. **`internal/` is never reachable with a user token,
and `v1/` is never reachable with a machine credential.** Keep that boundary.

## Four authentication paths

Resolved in `deps.py`. The bearer-token **prefix** (or, for runners, the route + shared secret)
decides which one you're on:

| Prefix | Principal | Dependency |
|---|---|---|
| `thp_` | API key | `_try_api_key_auth`, tried **first** |
| — (JWT) | User | `decode_access_token` fallback |
| `Bearer <shared secret>` + `X-Runner-Id` | Plugin runner | `get_plugin_runner_principal` |
| — (opaque) | Per-run plugin runtime token | `get_plugin_runtime_principal` |

- **API keys authenticate real requests today.** `_get_auth_context` tries the key
  first, then falls back to JWT. Keys carry their own `scopes` as permissions, are
  stored as a SHA-256 hash, honour `expires_at`, and get **no superadmin bypass** (the
  synthetic `AuthContext` user is always `is_superadmin=False`). If `X-Organisation-Id`
  is sent, it must match the key's org or the request is `403`.
- **JWT requests require the `X-Organisation-Id` header** — its absence is a `400`, not
  a `401`. Superadmins get the full `Permission` enum and skip the membership check;
  everyone else must be a member of that org.
- **Runner auth** constant-time-compares the bearer against `PLUGIN_RUNNER_SHARED_SECRET`
  (the whole trust boundary, shared by the API and every runner); `X-Runner-Id` is identity
  only and must match an existing `plugin_runner` row, else 404. Runners self-register via
  `POST /register` (shared secret, no token), which upserts the row + plugin inventory and a
  self-reported `base_url`, returning only the runner summary.
- **Runtime tokens** are per-`PluginRun`, hashed, and expiring. At terminal status the
  token stays resolvable; terminal status — not a nulled hash — is what rejects late
  runtime calls (409), which are audited as `rejected_late_result`.

## Choosing the right dependency

```python
CurrentUser              # authenticated user, no org context
SuperAdminUser           # superadmin only
OrgContext               # org resolved, permissions loaded
ActiveOrgContext         # active org enforced — JWT only
ActiveOrgOrApiKeyContext # active org — JWT or API key
```

**Default to `ActiveOrgOrApiKeyContext`** for org-scoped resources. Sixteen route
modules already use it; only seven remain JWT-only. Reach for `ActiveOrgContext` when a
route must be denied to programmatic clients (e.g. `proposed_actions.py`, where a human
approves a plugin's proposal — an API key approving its own proposals would defeat the
control).

## Permission guards

- `require_permission("write:case")` — org-level check against the resolved permission set.
- `require_case_permission("write:case")` — resolves the case through `CaseShare`,
  applies the **role pinned on that share**, and calls `_assert_writable`, which rejects
  writes to a `Duplicated` (merged-away) case.
- `require_case_owner()` — narrower still; the acting org must own the case, not merely
  have it shared.

Permissions are `read:`/`write:`/`run:` verbs per resource, defined in
`app/models/role.py`. Never hand-roll a permission string check inside a handler —
declare the guard as a dependency so it shows up in the OpenAPI schema.

## Route conventions

- One file per resource group under `v1/routes/`, aggregated in `v1/main.py`. Case-scoped
  children that share resolution logic live in `case_common.py` / `case_detail.py`;
  file up/download helpers in `_files.py`.
- Handlers **do not commit.** `get_session` is a unit-of-work: one transaction per
  request, committed when the handler returns cleanly. That is what makes a mutation and
  its `record_audit` rows atomic.
- Handlers map missing rows to `404`. CRUD returns `None`; it never raises `HTTPException`.
- All list endpoints return `Page[T]` (`items`, `total`, `skip`, `limit`) and accept the
  repeated `filter=key~op~value` params parsed by `app/crud/_filters.py`.
- Mutations call `record_audit(...)` in the same transaction. `RequestIdMiddleware` sets
  a `request_id` contextvar that correlates every row a request writes.

## Internal routes

`analyzer.py` and `responder.py` serve the konnect worker; `plugin_runner.py` (self-registration,
heartbeat, sync, run claim) and `plugin_runtime.py` (results, progress, file up/download) serve the
plugin runner.

`plugin_runtime.py` is the **enforcement point** for the eight plugin permissions.
That vocabulary is duplicated in `catlico-plugin-sdk/manifest.py` and the runner's install
validator — changing it is a three-repo change.

Plugin writes to canonical entities create `PluginProposedAction` rows for approval
rather than mutating directly. Approving one that can't be applied returns **HTTP 200
with `status: "failed"`** and a `decision_reason` — `decide()` swallows the internal
error. It does not surface a 422 to the client.
