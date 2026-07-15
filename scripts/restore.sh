#!/usr/bin/env bash
#
# catlico restore + verification — Phase 6 §6.3.
#
# Restores a backup produced by scripts/backup.sh (blob store + pg_dump), then
# runs a verification pass that fails loudly if the restored state is
# inconsistent:
#
#   1. alembic head match — the restored schema's revision equals the code's
#      head (a mismatch means the dump predates/postdates this checkout's
#      migrations; the app would refuse or misbehave).
#   2. seq high-water — every per-scope counter is still ahead of the max id it
#      has handed out (crud/_seq.py invariant). A counter that regressed would
#      re-hand an existing composite id. Checked for:
#        case_.next_task_seq       vs max(task.id)             per case
#        case_.next_attachment_seq vs max(attachment_link.id)  per case
#        task.next_log_seq         vs max(log.id)              per task
#   3. sampled blob-ref existence — a sample of attachment.sha256 rows must have
#      their blob present in the restored store (catches a truncated blob sync).
#
# Usage:
#   scripts/restore.sh BACKUP_DIR         # restore + verify
#   scripts/restore.sh --verify-only DIR  # skip restore, just run the checks
#
# DANGER: restore drops & recreates the target database. Point PG* / DATABASE_URL
# at the intended target and be sure.
set -euo pipefail

VERIFY_ONLY=0
if [[ "${1:-}" == "--verify-only" ]]; then VERIFY_ONLY=1; shift; fi
SRC="${1:?usage: restore.sh [--verify-only] BACKUP_DIR}"
[[ -d "${SRC}" ]] || { echo "[restore] no such dir: ${SRC}" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a; # shellcheck disable=SC1091
  . "${REPO_ROOT}/.env"; set +a
fi

log() { printf '[restore] %s\n' "$*" >&2; }
fail() { printf '[restore] FAIL: %s\n' "$*" >&2; exit 1; }

# --- resolve Postgres connection ---------------------------------------------
if [[ -n "${DATABASE_URL:-}" ]]; then
  export PG_CONN="${DATABASE_URL/+asyncpg/}"
else
  export PGHOST="${POSTGRES_SERVER:-localhost}"
  export PGPORT="${POSTGRES_PORT:-5432}"
  export PGUSER="${POSTGRES_USER:-catlico}"
  export PGPASSWORD="${POSTGRES_PASSWORD:-}"
  export PGDATABASE="${POSTGRES_DB:-catlico}"
  export PG_CONN=""
fi
# psql/pg_restore invoked with an explicit conn string when DATABASE_URL is set.
psql_q() {
  if [[ -n "${PG_CONN}" ]]; then
    psql "${PG_CONN}" -tA -c "$1"
  else
    psql -tA -c "$1"
  fi
}

# --- restore -----------------------------------------------------------------
if [[ "${VERIFY_ONLY}" -eq 0 ]]; then
  [[ -f "${SRC}/db.dump" ]] || fail "missing ${SRC}/db.dump"
  log "restoring database from ${SRC}/db.dump (clean + create)"
  if [[ -n "${PG_CONN}" ]]; then
    pg_restore --clean --if-exists --no-owner --no-privileges -d "${PG_CONN}" "${SRC}/db.dump"
  else
    pg_restore --clean --if-exists --no-owner --no-privileges -d "${PGDATABASE}" "${SRC}/db.dump"
  fi

  # Restore blobs. Immutable + content-addressed, so this is an additive sync
  # back to the live store; existing identical blobs are untouched.
  proto="${STORAGE_PROTOCOL:-local}"; root="${STORAGE_ROOT:-./var/blobs}"
  case "${proto}" in
    local)
      log "restoring local blobs -> ${root}/blobs"
      mkdir -p "${root}/blobs"
      rsync -a "${SRC}/blobs/" "${root}/blobs/"
      ;;
    s3)
      log "restoring blobs -> s3://${root}/blobs"
      endpoint=(); [[ -n "${S3_ENDPOINT_URL:-}" ]] && endpoint=(--endpoint-url "${S3_ENDPOINT_URL}")
      AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}" \
      AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}" \
      AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
        aws "${endpoint[@]}" s3 sync "${SRC}/blobs" "s3://${root}/blobs" --no-progress
      ;;
    *) fail "blob restore for STORAGE_PROTOCOL='${proto}' not implemented" ;;
  esac
fi

# --- verification pass -------------------------------------------------------
log "verification: alembic head"
CODE_HEAD="$(cd "${REPO_ROOT}" && uv run alembic heads 2>/dev/null | awk '{print $1}' | head -n1)"
DB_HEAD="$(psql_q "SELECT version_num FROM alembic_version LIMIT 1;" | tr -d '[:space:]')"
[[ -n "${DB_HEAD}" ]] || fail "alembic_version empty — schema not restored?"
if [[ "${CODE_HEAD}" != "${DB_HEAD}" ]]; then
  fail "alembic head mismatch: code=${CODE_HEAD} db=${DB_HEAD}"
fi
log "  ok (head=${DB_HEAD})"

log "verification: seq high-water invariants"
# A row per violated scope; empty result = all counters ahead of their max id.
SEQ_VIOLATIONS="$(psql_q "
  SELECT 'task', c.id, c.next_task_seq, max(t.id)
    FROM case_ c JOIN task t ON t.case_id = c.id
   GROUP BY c.id, c.next_task_seq HAVING c.next_task_seq <= max(t.id)
  UNION ALL
  SELECT 'attachment', c.id, c.next_attachment_seq, max(a.id)
    FROM case_ c JOIN attachment_link a ON a.case_id = c.id
   GROUP BY c.id, c.next_attachment_seq HAVING c.next_attachment_seq <= max(a.id)
  UNION ALL
  SELECT 'log', t.id, t.next_log_seq, max(l.id)
    FROM task t JOIN log l ON l.case_id = t.case_id AND l.task_id = t.id
   GROUP BY t.id, t.next_log_seq HAVING t.next_log_seq <= max(l.id);
")"
if [[ -n "${SEQ_VIOLATIONS}" ]]; then
  printf '%s\n' "${SEQ_VIOLATIONS}" >&2
  fail "seq high-water regression (counter <= max issued id) — restore is unsafe to write to"
fi
log "  ok"

log "verification: sampled blob-ref existence"
SAMPLE_N="${BLOB_SAMPLE_N:-25}"
SAMPLE="$(psql_q "SELECT sha256 FROM attachment ORDER BY random() LIMIT ${SAMPLE_N};")"
proto="${STORAGE_PROTOCOL:-local}"; root="${STORAGE_ROOT:-./var/blobs}"
missing=0; checked=0
blob_exists() { # $1 = sha256 -> matches core/storage.py sharding: blobs/<first2>/<sha>
  local sha="$1" shard="${1:0:2}"
  case "${proto}" in
    local) [[ -f "${root}/blobs/${shard}/${sha}" ]] ;;
    s3)
      endpoint=(); [[ -n "${S3_ENDPOINT_URL:-}" ]] && endpoint=(--endpoint-url "${S3_ENDPOINT_URL}")
      AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}" \
      AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}" \
      AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
        aws "${endpoint[@]}" s3api head-object --bucket "${root%%/*}" \
          --key "blobs/${shard}/${sha}" >/dev/null 2>&1 ;;
    *) return 0 ;;
  esac
}
while IFS= read -r sha; do
  [[ -z "${sha}" ]] && continue
  checked=$((checked + 1))
  blob_exists "${sha}" || { missing=$((missing + 1)); log "  MISSING blob ${sha}"; }
done <<< "${SAMPLE}"
if [[ "${missing}" -gt 0 ]]; then
  fail "${missing}/${checked} sampled blob refs missing from the store — blob sync incomplete"
fi
log "  ok (${checked} sampled, 0 missing)"

log "restore verified clean"
