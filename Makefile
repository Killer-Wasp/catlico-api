.PHONY: dev db install test migrate migration

# Start a local Postgres (and SeaweedFS) in the background
db:
	docker compose up -d db seaweedfs

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
