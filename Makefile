.PHONY: dev install test migrate migration

# Start the dev server with auto-reload (SQLite by default)
dev:
	uv run uvicorn app.main:app --reload

# Sync dependencies (including dev group)
install:
	uv sync

# Run the test suite
test:
	uv run pytest

# Apply migrations up to the latest revision
migrate:
	uv run alembic upgrade head

# Generate a new migration: make migration m="description"
migration:
	uv run alembic revision --autogenerate -m "$(m)"
