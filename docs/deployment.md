# Deployment

How to run `catlico-api` outside local dev. This is the control plane — the only service that
holds database credentials and the encryption key — so treat its secrets and network exposure
accordingly.

For the meaning and default of every individual variable, see **[configuration.md](configuration.md)**.
This page is the operational walkthrough: what to set, in what order, and why.

> Convention: examples set configuration as environment variables. A committed `.env` file works
> too (pydantic-settings reads both), but secrets are usually better injected by your platform's
> secret manager than written to disk.

## 1. Environment baseline

A minimal production environment:

```bash
ENVIRONMENT=production

# Auth / crypto — see §3. Generate real values, keep them identical across instances.
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(32))">
SECRET_ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Database — see §2
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@db-host:5432/catlico

# Web app origin — used for email links and CORS
FRONTEND_HOST=https://catlico.example.com
BACKEND_CORS_ORIGINS=https://catlico.example.com

# Cookies over HTTPS — see §5
COOKIE_SECURE=true

# First admin — see §4
DEFAULT_ADMIN_EMAIL=you@example.com
DEFAULT_ADMIN_PASSWORD=<a strong password>

# Durable blob storage — see §6
STORAGE_PROTOCOL=s3
STORAGE_ROOT=my-catlico-bucket
S3_REGION=us-east-1
S3_ACCESS_KEY=...
S3_SECRET_KEY=...

# Optional: SMTP for password-reset email (otherwise reset tokens are created but not delivered)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_FROM_EMAIL=no-reply@catlico.example.com
```

`ENVIRONMENT=production` has three effects: it disables the interactive docs (`/docs`, `/redoc`,
`/openapi.json`), it stops the startup path from auto-applying migrations, and it stops demo/dev
seeding. It also activates the startup guard that refuses to boot on an unset or weak `SECRET_KEY`.

## 2. Database and migrations

Postgres is the only supported engine — there is no SQLite fallback. Point the app at it with a
single `DATABASE_URL` (asyncpg driver) or the discrete `POSTGRES_*` vars; `DATABASE_URL` wins if
both are present.

**Migrations are not applied automatically outside local.** Only `ENVIRONMENT=local` brings the
schema to head on startup. In staging and production you run them yourself, as a deliberate step
before (or as part of) each release:

```bash
uv run alembic upgrade head    # or: make migrate
```

Run this once per deploy against the new code, before starting the app processes. Never point two
different code versions at one database mid-migration.

## 3. Secrets

Two secrets carry the whole security model. Generate both with real entropy and **set the same
value on every instance** — they are not interchangeable (see the
[SECRET_KEY vs SECRET_ENCRYPTION_KEY](configuration.md) notes).

| Secret | Generate | If it leaks | If you lose/rotate it |
|---|---|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Anyone can forge a session token for any user → full auth bypass | All outstanding tokens fail; every user is logged out. No data migration. |
| `SECRET_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Whoever also has a DB dump can decrypt stored connector/MISP/runner secrets | Existing ciphertext becomes undecryptable — you must re-encrypt stored rows first |

Consequences of getting these wrong in a multi-instance deployment:

- A **different** `SECRET_KEY` per instance means a token minted by instance A fails verification
  on instance B — users get logged out at random behind a load balancer. The random default does
  exactly this, which is why the production guard rejects it.
- **Rotating** `SECRET_ENCRYPTION_KEY` without re-encrypting is a data-loss event for stored
  secrets: `decrypt_*` returns empty and logs an error rather than raising. Back this key up
  wherever you back up the database, and rotate only with a re-encryption migration.

## 4. The first admin

On **every** startup, in **all** environments, the app reconciles a superadmin from
`DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD`:

- If no user with that email exists, it is created as an active superadmin.
- If it exists but its stored password does not match `DEFAULT_ADMIN_PASSWORD`, **the password is
  reset back to that value.**

That second rule is the surprising one: if you set `DEFAULT_ADMIN_PASSWORD` once, change the
admin's password in the UI later, then restart, the restart reverts it. Two safe patterns:

1. Treat `DEFAULT_ADMIN_PASSWORD` as the source of truth — set it to the real password (from your
   secret manager) and change it there, not in the UI.
2. Point `DEFAULT_ADMIN_EMAIL` at a throwaway/break-glass address, then create your real
   day-to-day admins through the UI; those are never touched by reconciliation.

Either way, change both values away from the `admin@example.com` / `changeme` defaults before you
expose the service.

## 5. TLS, reverse proxy, and cookies

The app speaks plain HTTP; terminate TLS at a reverse proxy (nginx, Caddy, an ALB, an ingress)
in front of it.

- **`COOKIE_SECURE=true`** (the default) is required in production. The refresh token rides an
  httpOnly, `SameSite=Lax`, `Secure` cookie scoped to the path `/api/v1/auth`. With `Secure` set,
  the browser only sends it over HTTPS — which is what it sees when your proxy terminates TLS, even
  though the proxy talks HTTP to the app. Setting it `false` in production would expose the refresh
  token on any plaintext hop.
- Because the cookie is `SameSite=Lax`, the web app and API should be served as the **same site**
  (typically the same apex domain). A cross-site split needs additional cookie/CORS work.
- **CORS:** browser origins are allowed only if they appear in `FRONTEND_HOST` or
  `BACKEND_CORS_ORIGINS`. Credentials are allowed, so these must be exact origins, not `*`.
- Forward the usual proxy headers (`X-Forwarded-Proto`, `X-Forwarded-For`) and let uvicorn trust
  them (`--proxy-headers --forwarded-allow-ips=*`, scoped to your proxy's address).

## 6. Storage

File attachments and file observables go to a blob store selected by `STORAGE_PROTOCOL`:

- `local` writes under `STORAGE_ROOT` on the API host's filesystem. It is **neither durable nor
  shared** — fine for a single-box trial, wrong for anything you can't afford to lose or that runs
  more than one instance.
- `s3` (incl. SeaweedFS/MinIO via `S3_ENDPOINT_URL`), `gcs`, `az`, `abfs` are the real options.
  Install the matching fsspec adapter. GCS/Azure read ambient credentials
  (`GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_STORAGE_CONNECTION_STRING`); S3 uses the `S3_*` vars or
  the ambient AWS credential chain.

`MAX_UPLOAD_BYTES` caps a single upload (default 100 MB).

## 7. Network surfaces

The app serves three route groups on one port, separated by who authenticates:

| Prefix | Principal | Expose to |
|---|---|---|
| `/api/v1/*` | User JWT or API key (`thp_…`) | Your users / the web app |
| `/api/internal/plugin-runner/*` | Per-runner machine credential (`cpr_…`) | Plugin runners only |
| `/api/internal/plugin-runtime/*` | Short-lived per-run token | Plugin runtime only |

If you don't run the plugin subsystem, the `/api/internal/*` routes still exist but authenticate
nothing external. Where your topology allows, restrict `/api/internal/*` at the proxy to the
network that hosts runners rather than the public internet.

## 8. Background workers run in-process

The API process also runs three background pollers (started in `lifespan`): the audit **outbox**
drainer (in-app feed, notifier delivery, WebSocket broadcast, plugin dispatch), the **plugin
maintenance** sweep (reap stuck runs, mark silent runners offline, retention), and the **plugin
push** loop. There is no separate worker/queue service to deploy — but there is a scaling caveat.

> **Run a single API process for now.** The outbox drainer selects undelivered rows **without**
> row-level locking, so two processes polling the same database will both pick up the same rows and
> can **deliver notifications and plugin pushes more than once**. This applies equally to multiple
> replicas *and* to multiple uvicorn/gunicorn workers in one container. Until the poller takes a
> lock (e.g. `SELECT … FOR UPDATE SKIP LOCKED` or an advisory lock), scale vertically and keep one
> instance. If you must scale HTTP horizontally, treat delivery as at-least-once and expect
> duplicates.

## 9. Running the process

Run uvicorn directly, without `--reload`, as a **single** worker (see §8):

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips="<proxy ip>"
```

Put a process supervisor (systemd, a container orchestrator) in front so it restarts on crash.
`uv sync` (or `make install`) installs dependencies; the same image can run migrations
(`alembic upgrade head`) as an init step before the server starts.

## 10. Health checks and readiness

There is **no unauthenticated health endpoint**, and in production `/docs` returns 404 (it's
disabled), so the local `curl /docs` readiness trick does not work in prod. Options:

- A **TCP check** on the listening port confirms the process is up.
- An **HTTP probe** against any `/api/v1/*` route treats a fast `401`/`403` as "process alive and
  serving" — the app answered, it just refused the unauthenticated request.

Startup itself is fail-fast: a missing/invalid `SECRET_ENCRYPTION_KEY`, a weak/unset `SECRET_KEY`
under `ENVIRONMENT=production`, or an unreachable/unconfigured database all raise during boot, so a
process that stays up has already passed those checks.

## 11. Optional: legacy connector worker

If you run `catlico-konnect` (still the production enrichment path), set `ANALYZER_SHARED_SECRET`
here to match the worker's `KONNECT_ANALYZER_SHARED_SECRET`, plus the `ANALYZER_*` lease knobs
documented in [configuration.md](configuration.md#legacy-connector-worker-catlico-konnect). The
core stack runs without it.

## Related

- [configuration.md](configuration.md) — every variable and the production checklist
- [architecture.md](architecture.md) — layers, multi-tenancy, the audit/outbox path
- [plugin-system.md](plugin-system.md) — runner/runtime surfaces and dispatch
- [getting-started.md](getting-started.md) — local setup and everyday commands
