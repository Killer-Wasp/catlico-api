.PHONY: dev db db-s3 dev-s3 install test migrate migration

# Start a local Postgres and the Mailpit mail catcher in the background.
# Blob storage defaults to the local filesystem (./dev/uploads) — no SeaweedFS.
# To exercise the S3 code path instead, use `make db-s3` / `make dev-s3`.
db:
	docker compose up -d db mailpit

# Start the dev server with auto-reload. Requires Postgres — run `make db` first.
dev: db
	set -a; \
	[ ! -f .env ] || . ./.env; \
	: "$${POSTGRES_SERVER:=localhost}"; \
	: "$${POSTGRES_PORT:=5432}"; \
	: "$${POSTGRES_USER:=catlico}"; \
	: "$${POSTGRES_PASSWORD:=catlico}"; \
	: "$${POSTGRES_DB:=catlico}"; \
	set +a; \
	uv run uvicorn app.main:app --reload

# Like `db`, but also start SeaweedFS (S3 gateway on :8333) for the S3 flow.
db-s3:
	docker compose up -d db seaweedfs mailpit

# Start the dev server against SeaweedFS's S3 gateway. Exports the S3_* knobs
# directly (not `:=`) so they override the blank values in .env — process env
# beats .env in pydantic-settings. A truthy S3_ENDPOINT_URL selects the S3 backend.
dev-s3: db-s3
	set -a; \
	[ ! -f .env ] || . ./.env; \
	: "$${POSTGRES_SERVER:=localhost}"; \
	: "$${POSTGRES_PORT:=5432}"; \
	: "$${POSTGRES_USER:=catlico}"; \
	: "$${POSTGRES_PASSWORD:=catlico}"; \
	: "$${POSTGRES_DB:=catlico}"; \
	S3_ENDPOINT_URL=http://localhost:8333; \
	S3_ACCESS_KEY=catlico; \
	S3_SECRET_KEY=catlico-secret; \
	STORAGE_ROOT=catlico; \
	set +a; \
	uv run uvicorn app.main:app --reload

# Sync dependencies (including dev group)
install:
	uv sync

# Run the test suite. Spins a throwaway Postgres via testcontainers; needs Docker.
test:
	uv run pytest

# Apply migrations up to the latest revision
migrate:
	uv run alembic upgrade head

# Generate a new migration: make migration m="description"
migration:
	uv run alembic revision --autogenerate -m "$(m)"
