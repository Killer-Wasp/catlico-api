# Configuration

Every setting is read by `app/core/configs.py` (pydantic-settings) from the environment or a
`.env` file. Defaults shown are the values in code.

Only one setting has **no usable default**: `SECRET_ENCRYPTION_KEY`. Startup fails without it.

## Core

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `local` | `local` \| `staging` \| `production`. `local` auto-applies migrations at startup; `production` disables `/docs`, `/redoc`, `/openapi.json`. |
| `SECRET_ENCRYPTION_KEY` | — | **Required.** A valid Fernet key. Encrypts plugin secrets and push-signing secrets at rest. Startup fails if absent or malformed. |
| `SECRET_KEY` | random per process | JWT signing key. **Set this explicitly** in any deployment — the default regenerates on restart, invalidating every token. |
| `FRONTEND_HOST` | `http://localhost:5173` | Used to build links in outbound email. |
| `BACKEND_CORS_ORIGINS` | — | Comma-separated origins. The dev web app runs on `:3000`. |
| `DB_ECHO` | `false` | Log every SQL statement. Noisy. |

## Database

Set the discrete `POSTGRES_*` values **or** `DATABASE_URL` directly. Startup fails fast if
neither is configured — there is no SQLite fallback.

| Variable | Default |
|---|---|
| `POSTGRES_SERVER` | — |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_USER` | — |
| `POSTGRES_PASSWORD` | `""` |
| `POSTGRES_DB` | `""` |
| `DATABASE_URL` | — |

`make db` starts a Postgres 16 container matching the local defaults (`catlico`/`catlico`/`catlico`).

## Authentication

| Variable | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `11520` | 8 days. Refresh tokens are persisted and re-read on `/auth/refresh`. |
| `DEFAULT_ADMIN_EMAIL` | `admin@example.com` | Seeded superadmin. **Change before any deployment.** |
| `DEFAULT_ADMIN_PASSWORD` | `changeme` | Likewise. |
| `DEFAULT_ADMIN_FIRST_NAME` | `Catlico` | |
| `DEFAULT_ADMIN_LAST_NAME` | `Administrator` | |

API keys carry the prefix `thp_` and their own `scopes`; they are stored as a SHA-256 hash and
never grant superadmin.

## Email / password reset

Password-reset delivery is a no-op unless SMTP is configured.

| Variable | Default |
|---|---|
| `SMTP_HOST` | — |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | — |
| `SMTP_PASSWORD` | — |
| `SMTP_FROM_EMAIL` | `no-reply@catlico.local` |
| `SMTP_USE_TLS` | `true` |
| `PASSWORD_RESET_PATH` | `/reset-password` |
| `PASSWORD_RESET_THROTTLE_SECONDS` | `300` |

## Storage

| Variable | Default | Notes |
|---|---|---|
| `STORAGE_PROTOCOL` | `local` | `local` \| `s3` \| `gcs` \| `az` \| `abfs` |
| `STORAGE_ROOT` | `./var/blobs` | Local blob root. |
| `MAX_UPLOAD_BYTES` | `104857600` | 100 MB. |

`make dev` starts a SeaweedFS S3 gateway on `:8333` for S3-compatible local testing.

## Plugin system

See [plugin-system.md](plugin-system.md) for what these govern.

### Enrollment and tokens

| Variable | Default | Notes |
|---|---|---|
| `PLUGIN_RUNNER_ENROLLMENT_TOKEN_TTL_SECONDS` | `900` | One-time enrollment token lifetime. |
| `PLUGIN_RUNTIME_TOKEN_TTL_SECONDS` | `900` | Per-run token lifetime. |
| `PLUGIN_RUNNER_SHARED_SECRET` | — | **Deprecated and unread.** Kept as a config knob while the enrollment flow rolled out. Nothing in the codebase reads it; it authenticates nothing. |

### Runtime file limits

| Variable | Default |
|---|---|
| `PLUGIN_RUNTIME_FILE_MAX_BYTES` | `26214400` (25 MB) |
| `PLUGIN_RUNTIME_FILE_MAX_COUNT` | `20` |
| `PLUGIN_RUNTIME_FILE_MAX_TOTAL_BYTES` | `104857600` (100 MB) |

### Maintenance sweep

| Variable | Default |
|---|---|
| `PLUGIN_MAINTENANCE_INTERVAL_SECONDS` | `30` |
| `PLUGIN_RUN_DEFAULT_TIMEOUT_SECONDS` | `60` |
| `PLUGIN_RUN_REAP_GRACE_SECONDS` | `60` |
| `PLUGIN_HEARTBEAT_INTERVAL_SECONDS` | `30` |
| `PLUGIN_RUNNER_OFFLINE_MISSED_HEARTBEATS` | `3` |
| `PLUGIN_RUN_RETENTION_DAYS` | `30` |
| `PLUGIN_DELIVERY_RETENTION_DAYS` | `7` |
| `PLUGIN_RESULT_RETENTION_DAYS` | `90` |

### Event push

| Variable | Default |
|---|---|
| `PLUGIN_PUSH_INTERVAL_SECONDS` | `5` |
| `PLUGIN_PUSH_BACKOFF_BASE_SECONDS` | `30` |
| `PLUGIN_PUSH_BACKOFF_CAP_SECONDS` | `900` |
| `PLUGIN_PUSH_MAX_AGE_SECONDS` | `86400` |
| `PLUGIN_PUSH_TIMEOUT_SECONDS` | `10.0` |

## Legacy connector worker (`catlico-konnect`)

| Variable | Default | Notes |
|---|---|---|
| `ANALYZER_SHARED_SECRET` | — | Must match the worker's `KONNECT_ANALYZER_SHARED_SECRET`. |
| `ANALYZER_LEASE_SECONDS` | `300` | |
| `ANALYZER_LEASE_SECONDS_MAX` | `3600` | |
| `ANALYZER_LEASE_GRACE_SECONDS` | `30` | |
| `ANALYZER_MAX_ATTEMPTS` | `3` | |
| `CONNECTOR_CACHE_TTL_SECONDS` | `86400` | |

## Functions

| Variable | Default | Notes |
|---|---|---|
| `FUNCTION_RUNNER_MODE` | `disabled` | `disabled` \| `stub`. **There is no real sandbox.** Both modes refuse to execute untrusted user code; a subprocess/jail sandbox is a later milestone. |

## Production checklist

- [ ] `ENVIRONMENT=production` (disables interactive API docs)
- [ ] `SECRET_KEY` set explicitly — the default is regenerated per process
- [ ] `SECRET_ENCRYPTION_KEY` set, backed up, and rotated deliberately (losing it makes every stored plugin secret unrecoverable)
- [ ] `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` changed
- [ ] `BACKEND_CORS_ORIGINS` restricted to your real frontend origin
- [ ] `ANALYZER_SHARED_SECRET` set if running `catlico-konnect`
- [ ] Migrations applied deliberately (`make migrate`) — auto-apply only happens when `ENVIRONMENT=local`
