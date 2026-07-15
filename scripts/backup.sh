#!/usr/bin/env bash
#
# catlico backup — Phase 6 §6.3.
#
# Blobs are immutable and never deleted, so the blob store is append-only. The
# ONLY safe ordering is: sync the blob store FIRST, then pg_dump. A dump taken
# after the blob sync can only reference blobs that were already copied — never
# a blob written after the sync — so a restore is guaranteed free of dangling
# attachment references. Doing it the other way round (dump then sync) could
# capture a row whose blob had not yet been copied.
#
# Usage:
#   scripts/backup.sh [BACKUP_DIR]
#
# Produces BACKUP_DIR/<timestamp>/ containing:
#   blobs/        — copy of the content-addressed blob store
#   db.dump       — pg_dump custom-format archive (pg_restore-compatible)
#   MANIFEST.txt  — provenance (timestamp, storage config, db identity)
#
# Connection/storage config is read from the same env the API uses (or a
# sourced .env). See docs/deployment.md.
set -euo pipefail

# --- locate repo root + optional .env ----------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"
  set +a
fi

BACKUP_ROOT="${1:-${BACKUP_DIR:-${REPO_ROOT}/backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${STAMP}"
mkdir -p "${DEST}/blobs"

log() { printf '[backup] %s\n' "$*" >&2; }

# --- resolve Postgres connection ---------------------------------------------
# Prefer DATABASE_URL; otherwise assemble from POSTGRES_* (as the app does).
if [[ -n "${DATABASE_URL:-}" ]]; then
  # Strip the SQLAlchemy driver suffix so libpq accepts it.
  export PG_CONN="${DATABASE_URL/+asyncpg/}"
else
  export PGHOST="${POSTGRES_SERVER:-localhost}"
  export PGPORT="${POSTGRES_PORT:-5432}"
  export PGUSER="${POSTGRES_USER:-catlico}"
  export PGPASSWORD="${POSTGRES_PASSWORD:-}"
  export PGDATABASE="${POSTGRES_DB:-catlico}"
  export PG_CONN=""
fi

pg() { if [[ -n "${PG_CONN}" ]]; then "$1" "${PG_CONN}" "${@:2}"; else "$@"; fi; }

# --- step 1: blob store (FIRST, append-only) ---------------------------------
sync_blobs() {
  local proto="${STORAGE_PROTOCOL:-local}"
  local root="${STORAGE_ROOT:-./var/blobs}"
  case "${proto}" in
    local)
      log "syncing local blob store ${root}/blobs -> ${DEST}/blobs"
      if [[ -d "${root}/blobs" ]]; then
        rsync -a --delete "${root}/blobs/" "${DEST}/blobs/"
      else
        log "WARNING: ${root}/blobs does not exist (nothing to back up)"
      fi
      ;;
    s3)
      log "syncing s3 blob store s3://${root}/blobs -> ${DEST}/blobs"
      local -a endpoint=()
      [[ -n "${S3_ENDPOINT_URL:-}" ]] && endpoint=(--endpoint-url "${S3_ENDPOINT_URL}")
      AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}" \
      AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}" \
      AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
        aws "${endpoint[@]}" s3 sync "s3://${root}/blobs" "${DEST}/blobs" --no-progress
      ;;
    gcs|az|abfs)
      log "ERROR: automated blob sync for '${proto}' is not implemented here."
      log "Use the provider's native tool (gsutil rsync / az storage blob sync)"
      log "to copy '${root}/blobs' into ${DEST}/blobs before the dump, then re-run"
      log "with STORAGE_PROTOCOL=local pointing at the copy, OR extend this case."
      exit 3
      ;;
    *)
      log "ERROR: unknown STORAGE_PROTOCOL '${proto}'"; exit 2 ;;
  esac
}
sync_blobs

# --- step 2: pg_dump (custom format) -----------------------------------------
log "dumping database -> ${DEST}/db.dump"
pg pg_dump -Fc --no-owner --no-privileges -f "${DEST}/db.dump"

# --- manifest ----------------------------------------------------------------
ALEMBIC_HEAD="$(cd "${REPO_ROOT}" && uv run alembic current 2>/dev/null | awk '{print $1}' | tail -n1 || true)"
{
  echo "catlico backup manifest"
  echo "timestamp        ${STAMP}"
  echo "storage_protocol ${STORAGE_PROTOCOL:-local}"
  echo "storage_root     ${STORAGE_ROOT:-./var/blobs}"
  echo "alembic_current  ${ALEMBIC_HEAD:-unknown}"
  echo "blob_count       $(find "${DEST}/blobs" -type f | wc -l | tr -d ' ')"
} > "${DEST}/MANIFEST.txt"

log "done -> ${DEST}"
echo "${DEST}"
