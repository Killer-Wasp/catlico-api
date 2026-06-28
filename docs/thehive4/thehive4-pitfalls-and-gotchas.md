# TheHive 4 — Pitfalls & Gotchas (what will sting a reimplementation)

> The landmines. If you build something TheHive-shaped, these are the things that will bite —
> drawn from the **TH4/Cortex source audit**, the **ScalliGraph framework audit** (the traps are
> worst where you move off JanusGraph onto SQL), and the **GitHub issue tracker** (defects and
> operational failures real users hit). For each: the trap → what breaks → **the lesson**.
>
> Companion to [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (§2 gap
> analysis names many of these; the mitigations live in its milestones) and
> [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md) (the
> pain-points section). Issue numbers are **as-mined from the trackers** — treat as pointers, not
> exact live state.

**Severity:** 🔴 correctness / data-loss · 🟠 operational / performance · 🟡 subtle behaviour / footgun.

---

## 1. Multi-tenancy & data isolation — the scariest ones 🔴

1. **Visibility is baked into every traversal — miss it once and you leak across orgs.** TheHive
   applies `.visible`/`.can` on *every* Gremlin query; the safety is only as good as the developer
   remembering it. *Lesson:* don't scatter the check — funnel **all** scoped reads through one
   gate (`scope_visible`) and add a **CI test that fails on any un-gated query**. On SQLite (no RLS)
   this gate is your *only* wall.
2. **The denormalized `organisationIds` array drifts.** TH4 caches org-visibility on
   case/observable/task for speed; if a share/unshare path forgets to update it, rows become
   invisible or over-visible. *Lesson:* treat it as a cache, derive truth from the share table, and
   run a **reconciliation integrity-check** that asserts `denorm == share-derived`.
3. **Postgres superusers and `BYPASSRLS` silently ignore RLS.** If your app connects as a
   superuser, every RLS policy is a no-op and you'll *think* you're isolated. *Lesson:* connect as a
   **non-superuser, non-BYPASSRLS role**; use `FORCE ROW LEVEL SECURITY` on tables so even the table
   owner is subject to policy (PoC V4 documents this).
4. **The live stream is a second leak path.** Audit ids pushed to a user's stream can reveal another
   org's activity unless you re-check visibility *per subscriber*. *Lesson:* apply a **second
   visibility gate** in the SSE/stream fan-out, not just on the REST read.
5. **Aggregations and joins forget the gate.** A `group by status` count or a join through
   observables can count rows the caller can't see. *Lesson:* re-apply the gate to **every scoped
   entity in a join/subquery**, including aggregation sources.

---

## 2. Audit, stream & event-consistency traps 🔴🟡

6. **`mainAction` is set only on the *last* audit of a transaction — and that last audit's `details`
   may be empty.** TH4 holds one pending audit and flushes the previous as secondary; the final one
   is flagged main-action at commit. Naively flagging the *first* (or every) audit breaks the
   activity flow. *Lesson:* replicate the deferred batching exactly; one main-action per request.
7. **Publish strictly on commit — never inline.** If you emit a stream/notification event during the
   request, a later rollback leaves the UI/alerts showing a phantom change. *Lesson:* stage events in
   an **outbox row in the same transaction**; a separate dispatcher publishes **after commit**.
8. **"Unaudited" transactions bypass batching.** Operations like merge intentionally suppress audit
   in TH4 (`unauditedTransactions`). If you don't model this, merge spams the timeline. *Lesson:*
   support an explicit "no-audit / merge-audit" mode.
9. **At-least-once delivery means duplicates.** Any durable-outbox dispatcher will occasionally
   redeliver. *Lesson:* make notifiers/consumers **idempotent** (dedupe on audit-id+topic).
10. **In-memory `StreamActor`/`FlowActor` state dies on restart and doesn't scale.** TH4's per-session
    actors hold the live-stream buffer in the JVM. *Lesson:* don't keep delivery state only in
    process memory; for HA, fan out via Redis pub/sub or PG `LISTEN/NOTIFY`.

---

## 3. Concurrency & write-path traps 🔴🟠

11. **Retry-on-conflict assumes side-effect-free, idempotent lambdas.** ScalliGraph's
    `tryTransaction` re-runs the whole block on a conflict. If your block sent an email or called an
    API, the retry **does it again**. *Lesson:* keep external side effects **outside** the
    transaction (that's what the outbox is for); only retry pure DB work.
12. **Case numbering is an actor in TH4 — naive SQL ports break differently.** A bare sequence leaves
    **gaps** on rollback (sometimes unacceptable for case numbers); a locked counter **serializes
    writes** and contends. *Lesson:* pick deliberately — sequence (gaps OK) vs locked counter (no
    gaps, slower) — and document it.
13. **Orphan cleanup has a race.** On unshare, TH4 deletes a task/observable iff it has no remaining
    shares (`filterNot(_.shares.range(1,2)).remove()`); concurrent unshares can both see "1 share
    left" and mis-delete or leak. *Lesson:* do orphan checks under the right lock/isolation, with a
    cleanup integrity-check as backstop.
14. **SQLite is single-writer — you *will* hit "database is locked."** Concurrent writes that work on
    Postgres deadlock or error on SQLite. *Lesson:* use **WAL mode + `BEGIN IMMEDIATE` + a
    busy-timeout**, keep write transactions short, and run the dispatcher single-process on SQLite.

---

## 4. The `/query` engine & field-registry footguns 🟡🔴

15. **`_contains` means "field/path EXISTS", not substring.** This ScalliGraph operator silently
    returns the wrong rows if you assume it's a `LIKE`. *Lesson:* map `_contains`→`IS NOT NULL`/
    `EXISTS`; substring search is `_like`. Add an explicit test asserting this.
16. **Predicate→backend translation is not 1:1.** `_like`/`_wildcard` need `ILIKE` + escaping on PG
    vs `LIKE`/GLOB on SQLite; collation and case-sensitivity differ; JanusGraph even rewrote `eq`→
    `startsWith` for some indexes. *Lesson:* centralize per-dialect predicate compilation and pin
    semantics with **golden fixtures run on both engines**.
17. **Computed/traversal filters drop the tenancy gate.** A filter on `assignee`/`tags`/`customFields`
    becomes a subquery/join; it's easy to forget the gate *inside* it. *Lesson:* the subquery must
    start from a gated source. (This is the same leak as #5, via the generic query path.)
18. **Stateless offset pagination degrades and drifts.** Deep `OFFSET` is O(offset); and if the sort
    isn't fully deterministic, pages overlap/skip rows as data changes. *Lesson:* use **keyset
    (seek) pagination** on `(sort_key, id)`; always include a tiebreaker id in the sort.
19. **`limitedCountThreshold` returns a *negative/`N+`* count past the threshold.** TH4 stops counting
    big result sets. If a caller treats that as a real total, totals/pagers break. *Lesson:* define
    and document the "≥N" sentinel; surface it as `N+` in the API.
20. **Serializer recursion has no cycle guard.** ScalliGraph's renderers compose `.list/.opt/.set`
    with no depth limit; a cyclic entity graph (case→linkedCase→…) infinite-loops / stack-overflows.
    *Lesson:* add **depth limits / visited-set** to output rendering.
21. **Aggregation time-bucketing differs by engine.** `date_trunc` (PG) vs `strftime` (SQLite) round
    and format differently; dashboards diverge. *Lesson:* one abstraction, tested on both.
22. **`IteratorOutput` materializes the whole result set in memory before serializing.** Large list
    responses balloon memory. *Lesson:* stream responses (chunked) and cap page sizes.

---

## 5. Data-model & schema-migration traps 🔴🟡

23. **The 4.0.5 freetags migration broke old-namespace tag queries.** TH4 moved loose tags into a
    per-org `_freetags_<orgId>` taxonomy; queries written against the old `namespace:predicate=value`
    shape stopped matching. *Lesson:* decide your tag model **up front**; if you ever migrate tag
    storage, ship a query-compat shim or rewrite saved searches.
24. **Cardinality coercion (single/option/list/set) and enums bite at the storage boundary.** A field
    that's single on one model and list on another, or an enum value added later, fails validation or
    mis-stores. Multi-value columns differ PG (`ARRAY`) vs SQLite (JSON). *Lesson:* one
    `ArrayOrJson`/enum-registry abstraction; validate enum values on write.
25. **Index creation/reindex is expensive and sometimes blocking.** JanusGraph reindex requires a
    full scan and explicit enablement; on PG, creating an index on a big table locks unless
    `CONCURRENTLY`. *Lesson:* plan index migrations for large tables (use `CREATE INDEX CONCURRENTLY`
    on PG; budget downtime windows).
26. **Custom fields weren't filterable/sortable everywhere — the classic retrofit pain.** Users asked
    repeatedly (#253) because CFs couldn't be searched/used as columns/dashboards. *Lesson:* make
    custom fields **first-class registry citizens from day one**, not a bolt-on.
27. **Computed properties are real query surface.** `handlingDuration` (case & alert) and `imported`
    are computed, but users filter/sort/aggregate on them. *Lesson:* the field registry must support
    **computed/resolver fields**, not just columns.
28. **Per-org `ResolutionStatus`/`ImpactStatus` can orphan cases.** They're per-organisation lookups;
    deleting one referenced by cases corrupts state (TH4 has integrity checks for exactly this).
    *Lesson:* guard deletes (refuse if referenced) and add an integrity-check.

---

## 6. Connector (Cortex / MISP) traps 🔴🟠🟡

29. **The Cortex report `operations` contract is *undefined by Cortex* — TheHive defines it.** Cortex
    just passes an opaque array back; the meaning of each operation lives in TheHive's
    ActionOperations. *Lesson:* you must **specify and own** the operation schema; don't expect Cortex
    to validate it.
30. **Responder `operations` are NOT audited in TH4.** Mutations applied from a responder bypass the
    audit trail (`O.unaudited`). *Lesson:* decide deliberately — we route connector writes through the
    normal **audited** service path as a scoped principal (an improvement, not a clone).
31. **Cortex job folders are local filesystem.** Distributed/k8s runners need a shared mount or
    artifact upload, and abandoned job folders leak disk. *Lesson:* abstract artifact I/O; clean up
    folders; don't assume one host.
32. **"Observable already exists" race on analyzer artifact creation (#1982).** Concurrent jobs adding
    the same artifact collide; reports vanish. *Lesson:* **upsert** observables on `(dataType, data)`
    and make artifact creation idempotent.
33. **TLP/PAP must be checked BEFORE the external call.** If you gate after dispatch, sensitive data
    already left. *Lesson:* enforce `max_tlp`/`max_pap` in the connector layer **prior** to invoking
    the worker.
34. **MISP import idempotency hinges on `(type, source, sourceRef, org)` + `lastSyncDate`.** Skip it
    and every sync re-creates alerts (a duplicate storm). *Lesson:* enforce the dedup key and window
    by sync date.
35. **MISP sightings aren't synced and promotion is manual in TH4.** Don't assume round-trip parity.
    *Lesson:* document what is/isn't synchronized; add explicit promotion rules if you want auto.
36. **Cortex job cache (`cacheTag`) can serve stale results.** Dedup by hash returns a prior report
    within the TTL — surprising when intel changed. *Lesson:* expose/curate the cache TTL; allow
    force-refresh.

---

## 7. Attachments & storage traps 🔴

37. **Hash-dedup means you must delete the blob only on the *last* reference.** TH4 reuses one blob
    for identical attachments (by hash); deleting when `useCount > 1` **corrupts other cases'
    attachments**. *Lesson:* ref-count blobs; delete bytes only when the last reference goes.
38. **Upload size limits bite quietly.** `play.http.parser.maxDiskBuffer` capped uploads. *Lesson:*
    set explicit limits and return a clear error, not a truncated/failed upload.
39. **Malware samples need safe handling.** TH4 supports password-protected zips for malicious files.
    *Lesson:* don't store raw malware unprotected; carry the same safe-handling option.

---

## 8. Authentication traps 🟠🟡

40. **Run the app as a non-superuser DB role** (see #3) — the single most important auth/DB
    interaction for isolation.
41. **OAuth2 is deprecated in TH5 in favour of OpenID.** Building fresh on plain OAuth2 is building on
    a sunset path. *Lesson:* target **OIDC**; keep the provider chain pluggable.
42. **Provider-chain order and `defaultUserDomain` normalization matter.** Wrong order or missing
    domain-normalization causes "user not found"/duplicate-identity bugs. *Lesson:* make chain order
    explicit and normalize logins consistently.
43. **Account lockout was missing (#2311).** Brute-force protection wasn't built in. *Lesson:* ship
    **lockout + session list/revoke + password reset** as table-stakes (in our M1/M9).

---

## 9. Operational landmines — why people actually churned 🟠🔴

44. **Cassandra/JanusGraph OOM, CPU spikes, instability (Cortex #214 — 47 comments; #1563, #1341).**
    The single loudest complaint; "reboot the VM every 2 days." *Lesson:* **don't reach for a graph
    DB.** Relational PG/SQLite removes this whole class of pain (our headline choice).
45. **Elasticsearch 8 / OpenSearch 2 incompatibility locked users on old versions (#2465; Cortex
    #429).** A hard ES dependency became a trap. *Lesson:* don't hard-depend on a specific search
    engine; PG/SQLite full-text first, OpenSearch **optional**.
46. **Migration silently lost alerts (#2188, #2238).** The scariest failure — data gone without a
    signal. *Lesson:* make import **idempotent + verifiable**, with a reconciliation report that
    refuses to claim success on a count mismatch.
47. **Search slowed badly on large datasets (#2116, #1959); alert creation took 20–25s (#1703).**
    *Lesson:* index deliberately, keyset-paginate, and benchmark on realistic volumes early.
48. **The v0/v1 API split confused everyone.** Two surfaces, two renderers. *Lesson:* ship **one
    clean v1**; don't carry legacy wire-compat unless explicitly required.

---

## 10. "Don't get stung" pre-flight checklist

- [ ] One gate for **all** scoped reads + a CI test that fails on un-gated queries (#1, #5, #17)
- [ ] App connects as a **non-superuser** role; `FORCE ROW LEVEL SECURITY` on PG (#3, #40)
- [ ] Second visibility gate on the live stream (#4)
- [ ] Events staged in an outbox, published **only after commit**; idempotent consumers (#7, #9)
- [ ] One **main-action** audit per request; support unaudited/merge mode (#6, #8)
- [ ] External side effects **outside** the retried transaction (#11)
- [ ] `_contains` = exists, not substring — with a test (#15)
- [ ] Per-dialect predicate compilation + **golden fixtures on PG *and* SQLite** (#16, #21)
- [ ] **Keyset** pagination with a deterministic tiebreaker (#18)
- [ ] Depth-limited serialization (no cyclic infinite loop) (#20)
- [ ] Custom fields + computed fields are first-class registry citizens (#26, #27)
- [ ] Blob ref-counting before delete (#37)
- [ ] Connector writes go through the **audited** service path; TLP/PAP checked **before** external calls (#30, #33)
- [ ] MISP/observable idempotency keys enforced (#32, #34)
- [ ] SQLite: WAL + `BEGIN IMMEDIATE` + busy-timeout; single-process dispatcher (#14)
- [ ] Migration is idempotent **and** verified with a reconciliation report (#46)
- [ ] No graph DB / no hard ES dependency (#44, #45)

---

## Cross-references

- Mitigations & where each lands: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (§2 gap analysis, §8 risks, §10 verification)
- The reported pain behind §9: [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md) §3
- Mechanisms in detail: [`thehive-api-internals.md`](./thehive-api-internals.md),
  [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md),
  [`cortex.md`](./cortex.md), [`thehive-misp-connector.md`](./thehive-misp-connector.md)
- Proven RLS predicates to transcribe: [`poc-postgres/`](./poc-postgres/)
