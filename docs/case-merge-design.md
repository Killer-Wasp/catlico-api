# Case Merge — Design + Implementation Plan (v1)

Status: **designed** (this doc), implementation in progress. Closes the explicit
"Case MERGE semantics" gap flagged in
the TheHive5 implementation plan (reference material kept outside this repo)
(*"which case wins, how tasks/observables/alerts/shares consolidate, number handling —
Under-specified everywhere"*). Source of truth for the data model is
the TheHive4 parity spec (reference material kept outside this repo; `case_merge`
table); this plan diverges from TheHive behaviour where the catlico codebase has its own
conventions, called out inline.

## Scope

**In (v1):** Case→Case merge of 2+ cases **owned by a single org** into one new case.

**Deferred (documented seams, not built):**
- **Cross-org merge** + share-union/intersection policy. v1 rejects mixed-owner merges.

**Also built (separate endpoint, `POST /api/v1/alerts/merge`):** Alert→Case bulk merge —
see *Alert→Case bulk merge* below.

## Model: create-new, not survivor

Merging sources `{#5, #9}` creates a **fresh** case `#12` that owns all consolidated
children. Sources are **frozen**, not deleted:

- `status = CaseStatus.duplicated` (the enum value already exists, previously unused)
- `resolution_status = duplicated`, `end_date = now`
- read-only (see *Freeze enforcement*)

They stay navigable so the activity feed, lineage, and timeline-union
(TheHive5 features design §2, reference material kept outside this repo) keep working —
you can't union events from deleted cases.

### Lineage: `case_merge` is the single source of truth

```
case_merge(source_case_id BIGINT, target_case_id BIGINT, PRIMARY KEY (source, target))
```

Both directions are derivable from this one table (target←sources, source→target), so it
is the **only** merge-lineage store. `Case.duplicate_of_case_id` is deliberately **left
untouched** by merge — it belongs to the separate manual "mark as duplicate" resolution
feature (no data movement), a distinct concept from merge (data consolidation).
Maintaining both for merge would be two-sources-of-truth that drift (the FK's
`ON DELETE SET NULL` could silently desync them).

"Number handling": the survivor gets a fresh `BIGSERIAL`; sources keep their numbers as
frozen tombstones.

## New-case scalar fields: caller supplies them

The endpoint takes an explicit `CaseCreate`-shaped payload for `#12`'s scalars
(title/description/severity/tlp/pap/summary/assignee/start_date). The frontend pre-fills
suggestions (max severity, concatenated title) but a human confirms — this dissolves the
"which case wins" ambiguity (backend stays deterministic) and handles the un-mergeable
fields (assignee, summary) without guessing.

**TLP/PAP floor guard (hard, regardless of payload):** reject the merge if the payload's
`tlp` or `pap` is *less* restrictive than any source's. Merging a TLP:RED case into a
TLP:GREEN result is a data-leak.

## Children: reparent (move, not copy)

Copying observables would corrupt similarity/dedup counts and double-count IOCs. Everything
moves; sources end up empty frozen markers.

| Entity | Link | Action | Collision |
| --- | --- | --- | --- |
| **Task** (+logs, task_share) | `case_id` | reparent `case_id` | none (own PK; children follow) |
| **Observable** (+enrichment, observable_share, obs comments) | `case_id` + `uq_observable_case_dedup` | reparent **with dedup** | **yes** |
| **Comment** (`entity_type=case`) | `entity_id` | reparent `entity_id` | none |
| **Tagging** (`taggable_type=case`) | `taggable_id` | reparent, **dedup** | **yes** (PK incl. case id) |
| **Flag** (`entity_type=case`, per-org) | `entity_id` | reparent, **dedup** | **yes** (PK incl. case id) |
| **Alert** | `case_id` | reparent `case_id` | none (import lineage follows survivor) |
| **CaseShare** | `case_id` | fresh owner share for `#12`; sources **kept** (frozen but navigable) | n/a |

### Observable collision resolution (union-and-reassign)

When source observables collide on `(observable_type, data)` in the target, keep one, and
for the survivor:

- `ioc = OR`, `sighted = OR`, `ignore_similarity = AND`
- `tlp = max` (most restrictive)
- `message` = concat of distinct non-empty
- reassign everything pointing at the twin (enrichments, observable comments,
  `observable_share`) to the survivor, **then** soft-delete the twin.

### Shares: dropped, not unioned (v1 single-org)

Reject the merge unless **all sources share one owner org**. `#12` gets a fresh owner
`CaseShare` for that org and **nothing else** — secondary org shares on the sources are
**not carried onto the new case** (they are recorded in the merge audit under
`shares_not_carried` so the owner can deliberately re-share). Unioning onto `#12` would be a
silent org-boundary data exposure; intersection would silently revoke access. Not-carrying
is the least-surprising *safe* default. The **source** cases keep their own shares untouched
— they stay navigable (read-only) to everyone who could already see them. (Cross-org
consolidation onto the survivor is the v2 follow-up.)

## Concurrency & atomicity

The whole operation is one request transaction (`record_audit` flushes; `get_session`
commits) — naturally all-or-nothing.

- `SELECT ... FOR UPDATE` on all source cases **ordered by `id` asc** (deadlock-free across
  overlapping merges). On Postgres this also blocks concurrent child inserts (their FK
  `FOR KEY SHARE` on the parent case conflicts with our `FOR UPDATE`), so nothing sneaks in
  between count and move.
- **Re-validate under lock.** A source is ineligible (aborts the whole merge) if:
  already `duplicated`/merged-away, soft-deleted, a different owner org, or the set has <2
  distinct cases. `resolved` (closed) cases **are** mergeable.

## Audit

- One `main_action=True` `merge` audit on `#12`: `details={"sources": [...], "moved":
  {"tasks": n, "observables": n, ...}, "shares_not_carried": [...]}`.
- One `main_action=False` `merge` audit per source: `details={"merged_into": 12}`.
- **No per-child audit spam** — the reparent moves are implied by the merge event.

## API

```
POST /api/v1/cases/merge
body: { "source_ids": [5, 9], "case": <CaseCreate scalars> }
-> 201 CasePublic   (the new case)
```

- Gated by `write:case` **AND** active org is owner of **every** source (replicates
  `require_case_owner` in a loop, since the per-path dependency can't see body ids). This
  also enforces the single-owner-org invariant.
- Plain `/merge` — catlico house style, not TheHive's `_merge`.

## Freeze enforcement

Single guard in `app/api/deps.py::_resolve_case_context`: when the requested permission is
a write (`write:*`), raise **409** if `case.status == duplicated`. One chokepoint covers
every case-scoped write route (update, task-add, observable-add, comment-add, …). Reads
pass through. The merge CRUD operates below this layer, so it freezes sources without
tripping its own guard.

## Lineage surfacing

`CasePublic` gains computed `merged_into: int | None` and `merged_from: list[int]`,
populated by a **single batched** `case_merge` lookup per page (never per-row / N+1). Most
pages return no lineage rows and cost ~nothing.

## Alert→Case bulk merge (`POST /api/v1/alerts/merge`)

Merge 1+ alerts into a single case — a **new** case, or an **existing** one via
`target_case_id`. Generalises the single-alert `/{id}/promote` and shares its per-alert
import path (`_import_alert_into_case`: observables deduped by `(type, data)`, tags unioned,
custom fields added only where the case has none, alert marked `Imported` with `case_id`).
Unlike case→case merge, alerts are **not** frozen tombstones — they remain as `Imported`
records pointing at the case (TheHive behaviour), so there is no `case_merge`-style lineage
table; the lineage is the alert's `case_id`.

Decisions (consistent with case→case above):
- **Single-org:** every alert must be owned by the acting org (alerts are org-owned, no
  sharing); cross-org alert ids return 404. Caller needs `write:alert` + `write:case`.
- **Eligibility:** alert must exist, be owned, and not already promoted
  (`case_id is None` and `status != Imported`) — else 409. Empty `alert_ids` → 422.
- **New case:** scalars derived — `severity/tlp/pap = max`, `title = title or first alert`,
  `description` concatenated, `start_date = min(alert.date)`. Optional `case_template_id`.
- **Existing case:** must be owned by the acting org (else 404) and not `duplicated` (else
  409 — the case→case freeze guard). **TLP/PAP raised** to the most restrictive among the
  alerts (never let an alert's data lose protection); the raise is recorded in the audit.
- **Audit:** one `merge` audit on the case (`details.alerts`, observable count,
  `tlp_pap_raised`); each alert keeps its own `promoted_to_case` audit via `mark_promoted`.

Tests: [tests/test_api_alert_merge.py](../tests/test_api_alert_merge.py).

## Test plan

- happy path: 2 cases → new case owns union of children; sources frozen + lineage set.
- observable/tag/flag dedup collisions.
- TLP/PAP floor rejection.
- mixed-owner-org rejection; <2 distinct rejection; already-duplicated source rejection.
- frozen source rejects writes (409) but serves reads.
- audit rows (one main on target, one per source, no child spam).
- `merged_into`/`merged_from` on detail + list (batched, no N+1).
