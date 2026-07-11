# Audit / Outbox — Implementation Plan (v1)

Status: **built** for case/task/log/observable/comment/alert (in CRUD) and user/org/member/role
(in the admin routes, since that CRUD takes no actor and is also called by `init_db` seeding).
See `app/crud/audit.py`, `app/models/audit.py`, `app/core/context.py`, migration `b1c2d3e4f5a6`,
tests `tests/test_crud_audit.py` + `tests/test_api_activity.py`. Remaining wiring (cascade child
rows, real consumers, global audit search) is listed under *Deferred seams*.

> **Current state (2026-07):** the "empty consumer registry" below describes v1 as designed.
> Four consumers are now registered at startup (notification feed, notifier delivery, WebSocket
> broadcast, plugin event dispatch) — see `app/main.py` and `docs/architecture.md`. The rest of
> this plan's mechanics (same-transaction write, post-commit drain, `attempts`, delivered
> marking) are still accurate.

Implementation note discovered during the build: `Case.__tablename__` is `case_` (escaping the SQL
keyword), but the polymorphic type string is normalised to `case` (trailing underscore stripped) so
it matches the `Comment`/`Flag`/`Tag` entity-type convention and the `context` values children pass.

Makes the convention in [`AGENTS.md`](../AGENTS.md) ("audit + outbox rows are written in the same
transaction as the mutation") real. Source of truth for the data
model is the TheHive4 parity spec (§ Audit, reference material kept outside this repo), but this plan diverges from the spec's
Postgres-flavored DDL where the catlico codebase has its own conventions — those divergences are
called out inline.

## Scope

**In:** write `audit` + `audit_outbox` rows transactionally on every top-level mutation; a drain
pipeline that runs end-to-end with an **empty consumer registry**; one tenant-scoped read endpoint.

**Deferred (documented seams, not built):** real outbox consumers (SSE/stream, notifications,
connector fan-out); global/admin audit search; per-child cascade audit rows; running the drain in a
dedicated worker process.

Rationale for the boundary: the invasive, do-it-once work is threading audit writes into every CRUD
mutation in the same transaction. That is identical whether or not consumers exist. The outbox table
is cheap to write alongside, so we write it now and avoid re-touching every CRUD module later. Only
the *consumers* are genuinely separate projects, so only they are deferred.

## Key decisions (with rationale)

| # | Decision | Why |
|---|----------|-----|
| Write mechanism | **Explicit** `record_audit(...)` call per CRUD mutation — not a SQLAlchemy flush hook | Audit is semantic (`create/update/delete/merge`, `main_action`, polymorphic `context`); a `before_flush` hook sees `delete_case` as ~15 row updates and can't recover intent. Soft-deletes are `UPDATE`s, so a generic hook would mislabel them. |
| Actor | **String column, no FK** (`str(user.id)` or `"system"`/`"analyzer"`) | `created_by` is already a stringified UUID but sometimes a sentinel; actors aren't always users, so the spec's `user_id BIGINT REFERENCES app_user` can't represent the analyzer/system. |
| `request_id` | **Contextvar** set by a tiny ASGI middleware (honours inbound `X-Request-Id`, else `uuid4`) | Pure cross-cutting correlation id; threading it through every CRUD signature would be noise. |
| `details` | **Field-level diff**, caller-supplied, central **redaction** denylist | Caller already has the diff (`model_dump(exclude_unset=True)` + pre-mutation object). SQLAlchemy attribute history is cleared on flush and CRUD flushes mid-request, so auto-diff is timing-fragile. Redaction is a security requirement — audit rows are a readable table + outbox payload. |
| JSON type | Generic `Column(JSON)`, **not JSONB** | Matches `enrichment.report` / `connector.manifest`. We never query *inside* `details` in v1, so JSONB's indexed-query benefits aren't needed. |
| Polymorphic target | `object_type: str` (from `__tablename__`) + `object_id: str`, **no FK**, indexed | Matches the existing `Comment`/`Flag`/`Tag` polymorphic idiom; sidesteps the mixed `int`/`uuid` PK collision; audit rows must survive hard-deletes of their target. |
| Cascade depth | **One `main_action=True` row per user action**; cascade children not individually audited | The case-delete row + shared `request_id` capture intent; children's `deleted_by`/`deleted_at` already record the rest. Avoids 15× audit writes per delete. |
| Outbox rows | **One row per audit** (`topic="audit"`), keep `UNIQUE(audit_id, topic)` for forward-compat | Zero consumers exist; pre-exploding into `stream/notification/connector` rows is guessing at routing. |
| Drain | **Lifespan background poller, empty consumer registry** | "Publish only after commit" rules out reusing the request session; a poller on its own session is decoupled, retryable (`attempts` column), and marks rows delivered so the table doesn't grow unbounded. One registry entry adds real fan-out later. |
| Read API | **One** endpoint `GET /cases/{case_id}/activity`, gated by `read:case` | Reuses existing `CaseShare` visibility — no `organisation_id` on audit, no post-filter. Global/admin search deferred. |
| Failure semantics | **Total on serialization** (coerce unknown types to `str`); **propagate on DB-write failure** | A `datetime` in `details` must not 500 a case edit; a failed audit-table write *should* roll back the mutation rather than silently lose the trail. |

## Data model — `app/models/audit.py`

```python
class Audit(table=True):
    id: int (pk)
    request_id: str
    action: str                 # create | update | delete | merge
    main_action: bool = True
    object_type: str            # from obj.__tablename__
    object_id: str
    context_type: str | None
    context_id: str | None
    actor: str                  # str(user.id) | "system" | "analyzer"  — no FK
    details: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime
    # Index("ix_audit_object", object_type, object_id)
    # Index("ix_audit_context", context_type, context_id)

class AuditOutbox(table=True):
    id: int (pk)
    audit_id: int               # FK audit.id, ON DELETE CASCADE
    topic: str = "audit"
    payload: dict = Field(sa_column=Column(JSON))
    delivered_at: datetime | None = None
    attempts: int = 0
    created_at: datetime
    # UniqueConstraint(audit_id, topic)
```

Register both in `app/models/__init__.py`.

## Work breakdown

1. **`app/core/context.py`** — `request_id` contextvar + ASGI middleware (reads `X-Request-Id`
   header or mints `uuid4`). Register middleware in `app/main.py`.
2. **`app/models/audit.py`** — `Audit`, `AuditOutbox` as above; export from `models/__init__.py`.
3. **`app/crud/audit.py`**
   - `record_audit(session, *, action, obj, context=None, actor, details=None, main_action=True)`:
     derive `object_type/id`; redact + coerce `details`; `add`+`flush` audit → `add` one outbox row
     (payload = serialized event: `request_id, action, object_type/id, actor, details, created_at`)
     → `flush`. **No commit.**
   - `dispatch_pending_outbox(session)`: select `delivered_at IS NULL`, hand to consumer registry
     (empty in v1 → debug-log), set `delivered_at`, bump `attempts`.
   - `list_case_activity(session, case_id, skip, limit)`: rows where `object=(case,id)` OR
     `context=(case,id)`, newest first, paginated via the `Page` envelope.
   - Redaction denylist constant: `hashed_password`, `password`, connector secret keys.
4. **Wire `record_audit` into CRUD mutations** — case, task, log, observable, alert (+promote),
   comment, case_share (share/unshare), user, org, member, role. One call per user action.
   **Case-scoped children pass `context=case`** (powers the activity feed).
5. **Drain poller** — start an asyncio task in `app/main.py` `lifespan`; own session; interval a few
   seconds; calls `dispatch_pending_outbox`. Empty consumer registry module (seam).
6. **Read endpoint** — `GET /cases/{case_id}/activity` in `routes/cases.py` (or `routes/audit.py`),
   gated by `require_case_permission("read:case")`.
7. **Migration** — one Alembic revision adding both tables + four indexes; stacks on current head
   (the 7 un-applied revisions in `alembic/versions/` stay put).

## Tests

1. **Unit (`record_audit`)** — txn writes 1 audit + 1 outbox; `object_type` from `__tablename__`;
   `context` set; redaction drops secret values but keeps field names; non-serializable `details`
   coerces instead of raising.
2. **Discipline test** — parametrized over every audited endpoint (create/update/delete case, task,
   log, observable, alert-promote, comment, share; admin: user/org/member/role); asserts an audit
   row with the right `action`/`object` appears. Catches "added an endpoint, forgot `record_audit`".
3. **Read-side tenancy** — `GET /cases/{id}/activity` returns case + child activity for a member;
   a different org with no share gets 404 (reuse existing case-visibility fixtures).
4. **Outbox drain** — `dispatch_pending_outbox` marks `delivered_at` + bumps `attempts`; call the
   function directly, do **not** run the live poller in tests (start it only under `lifespan`).

Explicitly **not** doing a reflection-based "every router has audit coverage" test in v1 — brittle;
the explicit parametrized list in #2 fails just as loudly.

## Deferred seams (where the next piece slots in)

- **Real consumers** — register an entry in the consumer registry; optionally expand to per-topic
  outbox rows (`stream|notification|connector`) in a purely additive migration.
- **Cascade child audits** — emit `main_action=False` rows sharing the `request_id` when an activity
  feed needs to show "3 tasks were closed by this action".
- **Global / admin audit search** — superadmin-gated query API over admin-entity audits (no case
  context).
- **Dedicated worker** — move the poller out of the app process; table-driven, so it's a deployment
  change, not a rewrite.
- **JSONB on `details`** — if a dashboard needs containment queries, switch one column to
  `.with_variant(postgresql.JSONB, "postgresql")`.
