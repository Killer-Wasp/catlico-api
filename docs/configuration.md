# Configuration

Every setting is read by `app/core/configs.py` (pydantic-settings) from the environment or a
`.env` file. Defaults shown are the values in code. Empty values are ignored
(`env_ignore_empty=True`), so an unset or blank var falls back to its code default.

Only one setting has **no usable default**: `SECRET_ENCRYPTION_KEY`. Startup fails without it.

Deploying to a real environment? Read the setting-by-setting rationale here, then follow
**[deployment.md](deployment.md)** for the end-to-end checklist (TLS, migrations, scaling,
secret backup/rotation).

## Core

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `local` | `local` \| `staging` \| `production`. Only `local` auto-applies migrations at startup and seeds data; `staging`/`production` require a manual `alembic upgrade head`. `production` also disables `/docs`, `/redoc`, `/openapi.json`. |
| `SECRET_ENCRYPTION_KEY` | — | **Required.** A valid Fernet key. Encrypts plugin secrets and push-signing secrets at rest. Startup fails if absent or malformed. Must be **identical across every instance** and backed up — see [deployment.md](deployment.md#3-secrets). |
| `SECRET_KEY` | random per process | JWT signing key. **Set this explicitly** in any deployment — the default regenerates on restart (invalidating every token) and differs per instance (breaking multi-instance auth). Must be the **same value on every instance**. |
| `SEED_PROFILE` | `demo` | JSON seed profile applied on startup, from a directory under `app/core/seed_data/`: `demo` (rich showcase), `dev` (minimal), or `none` (skip). **Only consulted when `ENVIRONMENT=local`** — never seeds in staging/production. |
| `FRONTEND_HOST` | `http://localhost:5173` | Origin of the web app. Used to build links in outbound email **and** auto-added to the CORS allowlist. Set to your real frontend URL in production. |
| `BACKEND_CORS_ORIGINS` | — | Comma-separated extra origins allowed for browser requests (in addition to `FRONTEND_HOST`). The dev web app runs on `:3000`. |
| `ATTACK_BUNDLE_URL` | MITRE CTI raw URL | Source of the MITRE ATT&CK enterprise STIX bundle imported into the catalog. Override to pin a version or point at an internal mirror for air-gapped installs. |
| `DB_ECHO` | `false` | Log every SQL statement. Noisy, and logs full statements/params — **never enable in production**. |

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
| `COOKIE_SECURE` | `true` | Marks the refresh-token cookie `Secure` (HTTPS-only). **Keep `true` in production** (the browser must reach the app over HTTPS, directly or via a TLS-terminating proxy). Set `false` only for plain-http local dev, or the browser silently drops the refresh cookie. |
| `DEFAULT_ADMIN_EMAIL` | `admin@example.com` | Superadmin reconciled on **every** startup, in **all** environments (not just `local`). **Change before any deployment.** |
| `DEFAULT_ADMIN_PASSWORD` | `changeme` | Password for that account. **Re-asserted on every boot:** if the stored password differs from this value, startup resets it back. So if you manage the admin password in the UI, either set this to that same password or point `DEFAULT_ADMIN_EMAIL` at a throwaway address and use a separate real admin. See [deployment.md](deployment.md#4-the-first-admin). |
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
| `STORAGE_PROTOCOL` | `local` | `local` \| `s3` \| `gcs` \| `az` \| `abfs`. Backed by fsspec — install the matching adapter for object stores. |
| `STORAGE_ROOT` | `./var/blobs` | Blob root: a directory for `local`, a bucket (optionally `bucket/prefix`) for object stores. |
| `MAX_UPLOAD_BYTES` | `104857600` | 100 MB. |

`local` writes to a directory on the API host — **not durable and not shared across instances**. Any multi-instance or containerised deployment should use an object store (`s3`/`gcs`/`az`). See [deployment.md](deployment.md#6-storage).

### S3 / SeaweedFS / MinIO (`STORAGE_PROTOCOL=s3`)

| Variable | Default | Notes |
|---|---|---|
| `S3_ENDPOINT_URL` | — | Custom endpoint for SeaweedFS/MinIO (e.g. `http://seaweedfs:8333`). Leave unset for AWS S3. |
| `S3_REGION` | `us-east-1` | |
| `S3_ACCESS_KEY` | — | Falls back to the ambient AWS credential chain if unset. |
| `S3_SECRET_KEY` | — | |

GCS and Azure read ambient credentials instead (`GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_STORAGE_CONNECTION_STRING`). `make dev` starts a SeaweedFS S3 gateway on `:8333` for S3-compatible local testing.

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

### Failure handling and retries

| Variable | Default | Notes |
|---|---|---|
| `PLUGIN_CONFIG_FAILURE_THRESHOLD` | `3` | Consecutive `config`-kind failures that auto-suspend an org's plugin. A passing config test (or any successful run) resets the counter. |
| `PLUGIN_TRANSIENT_MAX_ATTEMPTS` | `3` | Max attempts for a run that keeps failing with a `transient` error before it stays terminal. Only `transient` failures retry. |

### Event push

| Variable | Default |
|---|---|
| `PLUGIN_PUSH_INTERVAL_SECONDS` | `5` |
| `PLUGIN_PUSH_BACKOFF_BASE_SECONDS` | `30` |
| `PLUGIN_PUSH_BACKOFF_CAP_SECONDS` | `900` |
| `PLUGIN_PUSH_MAX_AGE_SECONDS` | `86400` |
| `PLUGIN_PUSH_TIMEOUT_SECONDS` | `10.0` |

## Outbox retention & failure handling

Retention windows and the dead-letter cap for the audit outbox and the in-app
notification tables. A background sweep prunes rows past these windows; see
`app/services/outbox_maintenance.py`.

| Variable | Default | Notes |
|---|---|---|
| `MAX_OUTBOX_ATTEMPTS` | `10` | Delivery attempts before an outbox row is dead-lettered (marked terminal, excluded from the drain) instead of retried forever. |
| `OUTBOX_RETENTION_DAYS` | `30` | Days a **delivered** outbox row is kept before pruning. |
| `OUTBOX_DEAD_LETTER_RETENTION_DAYS` | `90` | Days a **dead-lettered** outbox row is kept (longer, for post-mortem) before pruning. |
| `NOTIFICATION_READ_RETENTION_DAYS` | `90` | Days a **read** in-app notification (and its read receipts) is kept before pruning. Unread notifications are retained regardless of age. |
| `NOTIFIER_DELIVERY_RETENTION_DAYS` | `30` | Days a notifier delivery-ledger row is kept before pruning. |

## Legacy connector worker (`catlico-konnect`)

| Variable | Default | Notes |
|---|---|---|
| `ANALYZER_SHARED_SECRET` | — | Must match the worker's `KONNECT_ANALYZER_SHARED_SECRET`. |
| `ANALYZER_LEASE_SECONDS` | `300` | |
| `ANALYZER_LEASE_SECONDS_MAX` | `3600` | |
| `ANALYZER_LEASE_GRACE_SECONDS` | `30` | |
| `ANALYZER_MAX_ATTEMPTS` | `3` | |
| `CONNECTOR_CACHE_TTL_SECONDS` | `86400` | |

## Production checklist

- [ ] `ENVIRONMENT=production` (disables interactive API docs; stops auto-migrate/seed)
- [ ] `SECRET_KEY` set explicitly and **identical on every instance** — the default is random per process
- [ ] `SECRET_ENCRYPTION_KEY` set, **identical on every instance**, backed up, and rotated deliberately (losing it makes every stored plugin secret unrecoverable)
- [ ] `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` changed — remember the password is re-asserted on every boot
- [ ] `COOKIE_SECURE=true` and the app reachable over HTTPS (directly or via a TLS proxy)
- [ ] `FRONTEND_HOST` + `BACKEND_CORS_ORIGINS` restricted to your real frontend origin(s)
- [ ] `STORAGE_PROTOCOL` set to a durable object store (`s3`/`gcs`/`az`) for any multi-instance deployment — `local` is neither shared nor durable
- [ ] `SEED_PROFILE` irrelevant in production (seeding is skipped unless `ENVIRONMENT=local`)
- [ ] `ANALYZER_SHARED_SECRET` set if running `catlico-konnect`
- [ ] Migrations applied deliberately (`make migrate` / `alembic upgrade head`) — auto-apply only happens when `ENVIRONMENT=local`

The full walkthrough behind these items is in **[deployment.md](deployment.md)**.
