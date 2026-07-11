# CRUD conventions

Scope: everything under `app/crud/`. Complements the root `AGENTS.md`.

Async database operations, one file per entity, plus four shared helpers. Route handlers
call into here; **this layer is where multi-tenancy is enforced.**

## The non-negotiable rule

> Tenant isolation lives **inside the query**, never as a post-filter.

Case-scoped reads join through `CaseShare`; org-scoped reads filter on active-org
membership. Fetching rows and then dropping the ones the caller can't see is a data
leak the moment someone adds pagination — `total` would count rows the caller may not
know exist.

Soft-deletable tables must also filter `deleted_at IS NULL` in the same query.

## Shared helpers — use them, don't reinvent them

### `_seq.py` — id allocation

`next_task_ids`, `next_attachment_ids`, `next_log_ids` allocate composite-key child ids
from the parent's counter column via one `UPDATE … RETURNING` that post-increments under
the lock the UPDATE already takes. Atomic, gap-free per scope, never reused.

Pass `count=n` to claim a contiguous block in one round trip. Raises `LookupError` when
the parent row is missing.

Never scan siblings or use `MAX(id) + 1`.

### `pagination.py` — list queries

`paginate(session, base, *order_by, skip, limit)` runs `COUNT` over the filtered `base`
select, then an ordered page, returning `(rows, total)`. `base` must already carry the
tenant filter — the count is derived from it, so an unfiltered `base` leaks totals.

### `_filters.py` — the clause-based filter wire format

Every list endpoint (cases, tasks, alerts, observables) takes repeated
`filter=key~op~value` query params. **Terms are OR'd within a key and AND'd across
keys.** `op` is `eq` (exact) or `co` (case-insensitive contains).

This module owns wire parsing and tag grouping; each resource supplies its own
key→SQL mapping (see `case_filters.py` for the reference implementation).

- `parse_clauses` **silently drops malformed terms** (bad arity, unknown op, empty
  key/value) rather than erroring.
- `enum_condition` resolves values to enum members **in Python**. Binding a raw string
  raises at the driver, and `ilike` will not compile against a Postgres enum type. An
  unmatched value yields `false()` — the clause matches nothing, it does not blow up.
- Tags are `(namespace, predicate, value)` triples grouped into value-aware keys.
  `("tlp", "amber", "")` → key `tlp`; `("kill-chain", "phase", "exp")` → key
  `kill-chain:phase`. **Free tags (no namespace) are not filterable** — `tag_group_key`
  returns `None`. `tag_key_to_identity` is the exact inverse.

The wire format is mirrored in `catlico-web/src/lib/filters.ts`. **Changing the
serialization here breaks the web client** — change both sides together.

### `audit.py` — the audit/outbox write path

`record_audit()` writes one `Audit` row **and** one `AuditOutbox` row **in the caller's
transaction**, so a rolled-back mutation leaves no audit trail. Outbox rows are
dispatched only after commit, by the poller in `app/main.py`.

`register_consumer(fn)` appends to a module-level list; `app/main.py` registers four at
startup. `dispatch_pending_outbox` runs every consumer for a row inside the drain
transaction, catches each one's exceptions, and marks the row delivered **only if all
succeeded**. A failure therefore leaves the row undelivered, bumps `attempts`, and the
poller retries it — **re-running every consumer, including the ones that already
succeeded.**

> Outbox consumers must be **idempotent**. One failing consumer causes its siblings to
> run again on the next drain.

Polymorphic targets are string `(object_type, object_id)` pairs; `object_type` strips
the `case_` keyword-escape underscore to `case`.

## Transactions

`get_session` (`app/core/db.py`) is a unit-of-work: one transaction per request,
committed once if the handler returns, rolled back if it raises. Its docstring states
the rule — **CRUD must not commit** — and that is what lets a mutation and its audit +
outbox rows land atomically. No route handler calls `session.commit()`.

Two documented exceptions, so don't be misled by what you find:

- `dispatch_pending_outbox` commits on the **poller's own session**, outside any request.
  That is correct.
- The older admin-entity CRUD — `user.py`, `role.py`, `organisation.py`,
  `organisation_member.py` — still calls `session.commit()` inline. This predates the
  unit-of-work and is a **known inconsistency, not a pattern to copy.** New CRUD must
  leave the commit to `get_session`.

## Style

- Every function is `async` and takes `session: AsyncSession` first.
- Functions take and return model instances; they do not raise `HTTPException`. Mapping
  a missing row to a 404 is the route's job.
- Cascades are explicit. `delete_case` widens `delete_task`'s task→log cascade to the
  case scope; `CaseShare` rows are handled deliberately, not by DB cascade.
