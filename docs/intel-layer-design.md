# Intel-Layer Design Spike (Phase 5 §5.0)

_Status: GATE deliverable for OWNER REVIEW. Decision-support, not a build plan._
_Author: wave1-e spike. Date: 2026-07-15. Grounded in code at file:line where cited._

## Purpose & how to read this

This document exists to let the owner make **seven decisions** before any MISP end-to-end
(§5.1) or OpenCTI/STIX (§5.2) code is written. Each section states the problem, what the
current codebase already gives us (with file:line so the claims are checkable), the honest
tradeoffs, and a **Recommendation** that the owner can accept, reject, or amend. The final
section collects every recommendation into an explicit **Open decisions for owner** checklist.

Nothing here is committed to. The point of the gate is that the cheap decisions (abstraction
shape, library, placement) are locked before they get expensive to reverse.

### What the codebase already gives us (the substrate)

Before the seven points, the facts the whole design leans on:

- **MISP server CRUD is real and org-scoped.** `MispServer` (`app/models/misp.py:12-26`)
  stores `url`, `enabled`, `verify_ssl`, a Fernet-encrypted `auth_key_encrypted`, and a free
  `config: dict` (JSON column). CRUD (`app/crud/misp.py`) encrypts the auth key via
  `encrypt_secrets({"key": ...})` on create/update (`crud/misp.py:46,67`). The public shape
  never leaks the key — it exposes `has_auth_key: bool` only (`models/misp.py:56`,
  `routes/misp.py:47`). So **secret storage for MISP already exists and works.**
- **Only the I/O actions are stubs.** `routes/misp.py` implements servers list/create/patch/
  delete, but `/servers/{id}/test` (`:105-119`), `/servers/{id}/import-now` (`:122-137`), and
  `/cases/{id}/export/misp` (`:140-151`) all `raise HTTP_501_NOT_IMPLEMENTED`. The request
  shapes `MispImportRequest` (server_id, optional event_id) and `MispExportRequest`
  (server_id, `ioc_only=True`) already exist (`models/misp.py:62-71`).
- **The alert dedup key already matches MISP's natural key.** `Alert` has a partial-unique
  index `uq_alert_dedup` on `(type, source, source_ref, organisation_id)` where
  `deleted_at IS NULL` (`app/models/alert.py:34-44`). This is exactly
  `(misp, mispOrg, eventId, org)` from TheHive's connector
  (`docs/thehive4/thehive-misp-connector.md:78-80,137-139`). **The import leg needs no
  migration** — upsert on that key is idempotent by construction, and soft-deleted alerts
  don't block re-ingest.
- **Observables carry the flags the export/sightings legs need.** `Observable.ioc` and
  `Observable.sighted` are booleans (`app/models/observable.py:66-67`), surfaced in public/
  update shapes (`:106-107,129-130,144-145`). `ioc_only` export maps directly to `ioc`;
  sightings (if in scope) map to `sighted`.
- **STIX parsing precedent is hand-rolled, no library.** `app/services/attack_import.py`
  fetches the MITRE enterprise-attack STIX bundle with raw `httpx` (`:16-22`) and walks the
  STIX JSON as plain dicts (`parse_attack_bundle`, `:24-61`) — no `stix2`, no `pymisp`.
  Fetch and parse are deliberately split so tests never touch the network (`:1-7`). Neither
  `pymisp` nor `stix2` is a current dependency.
- **The plugin runner already has periodic-sync + org-scoped secrets machinery.**
  `schedule.fired` is a first-class trigger: `schedule_due_events`
  (`app/services/plugin_dispatch.py:245-`) emits deterministic per-`(plugin, org, cron-slot)`
  events (`_schedule_event_id`, `:239-242`), driven from the API-side maintenance sweep
  (`plugin_maintenance.py:242-252`), "never runner-side, so N runners cannot fire N times"
  (`plugin_dispatch.py:248-250`). Per-org config + secrets live in `PluginConfig`
  (`settings: dict` + `secrets_encrypted`, `models/plugin_runner.py:231-244`), delivered to
  the plugin as `{settings, secrets}` (`plugin_runner/engine.py:61,69-70`). `OrgPlugin`
  carries `schedule_override`, `auto_run_enabled`, and a config circuit-breaker
  (`suspended_reason`, `config_failure_streak`, `models/plugin_runner.py:213-223`).
- **The API's background work runs as in-process asyncio pollers with no leader election.**
  `_outbox_poller`, `_plugin_maintenance_poller`, `_plugin_push_poller` are
  `asyncio.create_task` loops in the lifespan (`app/main.py:39-113`). They assume a single API
  process — running two would double-fire. This is the "Phase 6 single-instance limit" the
  plan calls out.
- **The enterprise seam exists.** `app/core/extensions.py` is the OSS-side foundation the
  private `catlico-enterprise` package plugs into via the `catlico.extensions` entry point;
  inert with nothing installed, it can mount routers and run `on_startup` hooks
  (`main.py:105`). This is where "multi-platform fabric" lands without forking the core.

---

## 1. Unified intel-source abstraction & the interchange model

**Problem.** MISP and OpenCTI are different shapes. MISP is Events-of-Attributes over a REST
API; OpenCTI is a STIX-2.1 knowledge graph over GraphQL. We need one internal notion of
"intel source" so the platform doesn't grow a bespoke subsystem per vendor — and we need to
decide whether STIX 2.1 is our internal interchange model or whether each source maps
straight onto Catlico entities.

**What "an intel source" is.** A named, org-scoped, credentialed remote that Catlico can
**pull from** (import) and optionally **push to** (export), governed by a typed sync policy.
`MispServer` is already exactly this minus the vendor-neutral name. The two canonical mappings
are fixed by the existing data model and are the same for every source:

- **Events / reports / groupings → Alerts** (the container becomes an alert; the analyst
  promotes to a case). Dedup on `(type, source, source_ref, org)`.
- **Attributes / indicators / observed-data → Observables** on that alert, datatype resolved
  through a configurable type map. IOC-ness → `Observable.ioc`.

That mapping is stable regardless of interchange format. The real question is what sits in the
**middle**.

**Option A — STIX 2.1 as the universal internal interchange.** Every source normalizes to
STIX SDOs/SCOs; one STIX→Catlico mapper feeds alerts/observables; export runs Catlico→STIX
then STIX→vendor. Attractive because OpenCTI _is_ STIX natively and MISP ships a documented
STIX 2.1 export.

- Cost: STIX 2.1 is a large, relationship-heavy graph model. Forcing MISP events through
  STIX-as-intermediate means MISP→STIX→Alert instead of MISP→Alert — two lossy hops where one
  suffices, for the connector we're actually shipping first. The MITRE precedent
  (`attack_import.py`) shows we consume STIX fine as **plain dicts** when the source is STIX;
  it does not argue for making STIX our internal bus.

**Option B — per-source direct mapping to Catlico entities.** Each connector maps its own wire
format straight to Alert/Observable. MISP event→alert directly (the TheHive design,
`thehive-misp-connector.md:59-89`); OpenCTI STIX→alert directly when we build it.

- Cost: two mappers instead of one shared STIX mapper. But they share the _target_ (Alert +
  Observables + the type map + the dedup key), so the divergence is only the parse front-end,
  which is irreducible anyway (REST/JSON vs GraphQL/STIX).

**Option C — thin abstraction now, STIX as an interchange only where a source is natively
STIX.** Define a small internal `IntelSource` protocol — `test()`, `pull(policy) ->
Iterable[NormalizedEvent]`, `push(case, policy)` — where `NormalizedEvent` is a Catlico-shaped
DTO (title, source_ref, severity/tlp, list of typed observables), **not** STIX. MISP maps its
JSON to `NormalizedEvent` directly. When OpenCTI arrives, its connector parses STIX (as dicts,
per the MITRE precedent) into the same `NormalizedEvent`. STIX 2.1 becomes an
**on-the-wire interchange format for STIX-native sources and for the enterprise STIX I/O
feature** — not a mandatory internal representation everything is forced through.

**Recommendation: Option C.** Define the minimal `IntelSource` seam and a Catlico-shaped
`NormalizedEvent`/`NormalizedObservable` DTO as the internal contract. Do **not** adopt STIX
2.1 as the universal internal bus. STIX earns its place only at the edges — parsing STIX-native
sources (OpenCTI, MISP's STIX export) and emitting STIX for the enterprise interchange feature —
and even there we consume/produce it as dicts, matching the working `attack_import` precedent
rather than taking on the `stix2` object model. This keeps the OSS MISP connector a single
direct hop, keeps the shared surface (target entities + type map + dedup key) genuinely shared,
and leaves the STIX graph model as an enterprise concern where its complexity is justified.

---

## 2. Connector placement

**Problem.** Where does the sync engine physically run? Three candidates, each with real
consequences the plan already named:

**Option A — in-API service + poller.** A `misp_sync` service plus a new asyncio poller in the
lifespan (alongside `_outbox_poller` et al., `main.py:39-113`).

- Pro: the alert-dedup substrate, the DB session, `encrypt_secrets`, and the Alert/Observable
  CRUD all live here already. `import-now` is a synchronous request handler filling the
  existing 501 (`routes/misp.py:122`). Zero new moving parts, zero cross-process contract.
- Con: **inherits the single-instance limit.** The pollers are in-process asyncio with no
  leader election (`main.py`); a scheduled MISP poll would double-fire under a future
  multi-instance (Phase 6/HA) deploy. Network I/O against a slow MISP server runs inside the
  API process. This is the "inherits Phase 6 single-instance limits" tradeoff verbatim.

**Option B — runner plugin.** A MISP connector plugin on the plugin runner, triggered by
`schedule.fired`, with config+secrets via `PluginConfig`.

- Pro: **the periodic-sync machinery already exists and is already de-duped for
  multi-runner** — `schedule_due_events` fires once per `(plugin, org, cron-slot)` from the
  API sweep (`plugin_dispatch.py:245-250`), org-scoped secrets ride in
  `PluginConfig.secrets_encrypted` (`models/plugin_runner.py:243`), and there's a config
  circuit-breaker + `auto_run_enabled` (`OrgPlugin`, `:213-223`). Sync I/O runs off the API
  process, in the sandbox.
- Con: the connector would write alerts/observables **through the plugin result/proposed-action
  path**, not by directly upserting on the dedup key — meaning the clean idempotent
  `(type,source,source_ref,org)` upsert (the whole reason the import leg needs no migration)
  gets indirected through `PluginResult`/`PluginProposedAction`, which are designed for
  enrichment-on-an-entity, not for _creating_ alerts. Ingest-as-plugin also duplicates the
  MISP secret store we already have on `MispServer`. And `import-now` (analyst clicks a button,
  wants a synchronous answer) fits an API request far better than an async plugin dispatch.

**Option C — hybrid: in-API import-now/test/export, plugin (or in-API poller) for scheduled
sync.** The interactive legs (`test`, `import-now`, `export-case`) are API handlers filling the
existing 501s and writing directly on the dedup key. Scheduled background sync is a separate
concern decided independently.

**Recommendation: Option C, with scheduled sync as an in-API poller for OSS and the
`schedule.fired` plugin path reserved as the multi-instance/enterprise answer.** Concretely:

1. **`test`, `import-now`, `export-case` → in-API**, filling the three existing 501 stubs
   (`routes/misp.py:105,122,140`). They belong next to the dedup substrate and the encrypted
   `MispServer.auth_key`; they're synchronous by nature; they need no new cross-process
   contract. This is also exactly what §5.1's outline already scopes.
2. **Scheduled sync → an in-API poller in OSS**, iterating enabled `MispServer`s whose policy
   `purpose` includes import, reusing the same `import-now` code path. Accept the single-instance
   limit **as a documented constraint** — it's the same constraint every existing poller already
   has (`main.py`), so we're not adding a _new_ class of problem, and Phase 6/HA will need a
   leader-election answer for _all_ the pollers at once, not just this one.
3. **Do not build the connector as a runner plugin now.** The plugin path's real strength —
   `schedule.fired` already de-duped across runners — solves a problem (multi-instance
   scheduling) we don't have in OSS single-instance and that Phase 6 will solve globally. Its
   cost (indirecting alert creation through `PluginResult`, duplicating the MISP secret store) is
   paid immediately. Keep it on the table as the **migration target if/when** scheduled intel
   sync needs to survive HA before Phase 6 delivers poller leader-election — but that's a
   revisit, not a now-decision.

Rationale in one line: **the interactive legs have no reason to leave the API, and the one thing
the plugin path buys us (safe multi-instance scheduling) is a Phase 6 problem, not a Phase 5
one.**

---

## 3. Sync-policy config shape on `MispServer.config`

**Problem.** `MispServer.config` is an untyped `dict` JSON column (`models/misp.py:26`). The
TheHive connector's whole safety story is a **typed per-server policy** — filter at fetch time,
map types via config not code (`thehive-misp-connector.md:42-56,140-145`). We need to pin the
schema so the connector reads a validated policy, not an ad-hoc bag.

**Recommendation.** Define a Pydantic `MispSyncPolicy` model that is parsed _from_
`MispServer.config` (validated on write in the CRUD layer, so a bad policy 422s instead of
failing mid-sync). Proposed shape, lifted directly from the fields TheHive proved necessary:

```jsonc
// MispServer.config, validated as MispSyncPolicy
{
  "purpose": "import" | "export" | "import_export",   // gates each leg; default "import"
  "import": {
    "max_age_days": 30,          // only pull events newer than this (fetch-time filter)
    "max_attributes": 1000,      // skip oversized events
    "published_only": true,      // never pull drafts
    "org_whitelist": ["..."],    // MISP orgs to include (empty = all)
    "org_exclusion": ["..."],
    "tag_whitelist": ["tlp:*"],  // include filter
    "tag_exclusion": ["workflow:*"],
    "case_template_id": 42       // template applied when the imported alert is promoted
  },
  "export": {
    "ioc_only": true,            // default; overridable per-request via MispExportRequest
    "include_case_tags": true,
    "include_observable_tags": false,
    "extend_source_event": true  // if the case came from a MISP alert, extend that event
  },
  "attribute_type_map": {        // MISP (category,type) -> Catlico observable datatype
    "Network activity/ip-dst": "ip",
    "Payload delivery/sha256": "hash"
    // falls back to a logged warning on unmapped, per TheHive
  }
}
```

Key points for the owner:
- **Type map is config, not code** — the explicit reimplementation note
  (`thehive-misp-connector.md:140-142`). Ship a sensible default map seeded server-side so an
  empty policy still works; ops extend it without a release.
- **Filters live under `import` because they're fetch-time** — `published_only`, `max_age`,
  and the whitelists must be pushed into the MISP query, not applied after pulling the whole
  instance (`:143-145`).
- **`purpose` gates the legs** so an import-only server physically cannot be exported to (the
  `canExport` check, `:96`). Default `import` — the safe, read-only direction.
- Validate on CRUD write. `MispServerCreate.config`/`Update.config` are already free dicts
  (`models/misp.py:38,47`); the change is to parse them through `MispSyncPolicy` and reject
  invalid ones, storing the normalized form back.

**One open sub-decision:** whether `attribute_type_map` lives per-server (max flexibility, but
duplicated across servers) or as an org-level default map with per-server overrides. Recommend
**org-level default + per-server override merge** — most orgs want one map. Flagged below.

---

## 4. Library choice: `pymisp`/`stix2` vs hand-rolled `httpx`

**Problem.** Do we take on `pymisp` (and later `stix2`) as dependencies, or hand-roll the REST
calls with `httpx` as `attack_import` already does?

**The precedent is unambiguous.** `attack_import.py` consumes a STIX 2.1 bundle with raw
`httpx` + dict-walking, **no `stix2` library** (`:16-61`), and structures fetch/parse split for
testability. Neither `pymisp` nor `stix2` is currently a dependency.

**What we actually need from MISP for §5.1:** `GET /servers/getVersion` (test),
`POST /events/restSearch` with the policy filters (import), `GET`/download attribute samples,
and `POST /events/add` (export). That's a handful of documented JSON endpoints. `pymisp` is a
large dependency (it pulls its own transitive tree and mirrors the whole MISP object model)
for what is, at our scope, four REST calls behind a Fernet-decrypted key.

- `pymisp` pro: object model, built-in type constants, handles MISP quirks. Con: heavy dep,
  its object model is a second internal representation competing with our `NormalizedEvent`
  (§1), and it wants to own the HTTP client (we want our own `verify_ssl` from
  `MispServer.verify_ssl` and our own timeouts/SSRF posture).
- Hand-rolled `httpx` pro: matches the precedent, small surface, fetch/parse split gives
  network-free tests (which §5.1 explicitly wants — "mocked-MISP import idempotency"), full
  control over TLS/timeout/redirects. Con: we own the endpoint quirks and the type constants.

**Recommendation: hand-rolled `httpx`, mirroring `attack_import`'s fetch/parse split.** Build a
thin `MispClient` wrapping the four endpoints, taking `(url, decrypted_key, verify_ssl,
timeout)` from the `MispServer` row, returning parsed dicts that a pure mapper turns into
`NormalizedEvent`. This matches the established precedent, keeps tests network-free, and avoids
a heavy dependency for a small REST surface. **Do not add `stix2` either** — when STIX parsing
arrives (OpenCTI/enterprise), consume it as dicts exactly as `attack_import` does; revisit a
STIX library only if the relationship-graph handling in the enterprise feature proves too
error-prone by hand (a §5.2 decision, not now).

---

## 5. Sightings & export-leg semantics

**Problem.** Two related questions: are MISP **sightings** in scope, and what exactly does the
export leg do?

**Sightings.** TheHive's connector explicitly **does not** synchronize sightings — it imports
events/attributes and exports IOC observables, and treats its own `Observable.sighted` flag as
separate and local (`thehive-misp-connector.md:148-152`). We have the same `sighted` flag
(`models/observable.py:67`).

- **Recommendation: sightings OUT of scope for §5.1 (OSS MISP).** Match TheHive: import
  events→alerts, export IOC observables, keep `sighted` local. Pushing/pulling sightings is a
  bidirectional-intel-feedback feature — it belongs with the enterprise multi-platform fabric
  (§6), where "did anyone see this IOC" is a cross-org/cross-platform question worth the
  round-trip. Note it explicitly as deferred so it's a conscious choice, not an omission
  (the doc itself flags "add this explicitly if your workflow depends on it", `:151-152`).

**Export-leg semantics.** The export path (`export-case`, currently 501 at `routes/misp.py:140`)
should follow the TheHive sequence (`thehive-misp-connector.md:92-107`):

1. **`purpose` check** — server policy must allow export (§3), else 403.
2. **Select IOC observables.** `ioc_only` (default `True`, `MispExportRequest.ioc_only`,
   `models/misp.py:71`) maps straight to `Observable.ioc == True`
   (`models/observable.py:66`). `ioc_only=False` exports all observables. **This is the
   `ioc_only → observable flag` mapping the plan asked us to confirm — it's a direct field
   match, no new plumbing.**
3. **Map each observable → MISP attribute** via the reverse of the §3 `attribute_type_map`;
   include observable tags iff policy `include_observable_tags`.
4. **Dedup attributes**, then **create the MISP event** (info from the case, case tags iff
   policy `include_case_tags`). If the case originated from a MISP alert, **extend** that
   source event (`extend_source_event`).
5. **Write back a linking alert** referencing the new MISP event id, so the round-trip is
   visible and a subsequent import dedups on `(misp, org, newEventId, org)` instead of looping
   (`thehive-misp-connector.md:104-106,145`). This write-back is what prevents export→import
   ping-pong and is **not optional**.

- **Recommendation:** implement export as above, IOC-only by default, with the mandatory
  write-back linking alert. The `ioc_only`↔`ioc` mapping is confirmed and needs no model
  change.

---

## 6. Enterprise boundary

**Problem.** Where's the OSS/enterprise line for the intel layer?

The tiering doc is explicit and already decided at the strategy level: **basic MISP
import/export = OSS Core** ("MISP is free/open; matching it free is table-stakes +
community-requested", `enterprise.md:53,98,121`); **STIX 2.1 import, OpenCTI, bi-directional
intel sync = Enterprise** (`enterprise.md:54,80`), the "multi-platform intel fabric". This
matches §5.2 ("Lives in `catlico-enterprise` per tiering") and the memory note
(threat-intel-platform-direction: MISP+OpenCTI integration layer is future/enterprise).

**Where the seam physically is.** The enterprise extension seam already exists
(`app/core/extensions.py`, mounted in the lifespan `main.py:105`): the private
`catlico-enterprise` package plugs in via the `catlico.extensions` entry point, mounts its own
routers, and runs `on_startup` hooks — **inert in OSS**. So the boundary is enforceable without
forking: OSS ships the MISP connector in-tree; enterprise ships OpenCTI/STIX as an extension.

**Recommendation — draw the line here:**

| Capability | Tier | Why |
|---|---|---|
| MISP server CRUD, `test`, `import-now`, `export-case`, scheduled MISP sync | **OSS Core** | The §5.1 build. Table-stakes MISP parity; already substrate-complete. |
| The `IntelSource` seam + `NormalizedEvent` DTO (§1) | **OSS Core** | It's the abstraction MISP itself uses; keeping it OSS is what lets enterprise plug in cleanly. |
| Sync policy schema on `MispServer.config` (§3) | **OSS Core** | Part of the free MISP connector. |
| OpenCTI connector (GraphQL) | **Enterprise** | Multi-platform fabric; `enterprise.md:54,80`. |
| STIX 2.1 import/export as an interchange feature | **Enterprise** | Cross-platform interchange; not needed for MISP-direct. |
| Cross-org / federated intel sharing, sightings sync (§5) | **Enterprise** | Governed multi-org fabric; the paid pillar. |

The one nuance worth stating: **the `IntelSource` protocol is OSS, its second implementation is
enterprise.** MISP lives in-tree implementing the seam; OpenCTI lives in `catlico-enterprise`
implementing the same seam via the extension registry. That way "multi-platform" is literally
"a second registered source", and the OSS core never imports enterprise code.

---

## 7. The deferred `PluginResult` normalized columns

**Problem.** The plugin-runner plan deferred a set of `PluginResult` columns "until a real
plugin emits them" — `schema_refs`, `stix_refs`, `ocsf_class`, `attack_refs`, `severity`,
`first_seen_at`/`last_seen_at` — with the JSON `normalized` payload carrying them meanwhile
(`docs/catlico-plugin-runner-replacement-plan.md:756`, i.e. the "Deferred columns" bullet).
The spike must decide: **does the STIX interchange revive them, or do they stay dead?**

**Analysis.** These columns are about **enrichment output attached to an entity by a plugin** —
"this observable, per this analyzer, has these STIX refs / this ATT&CK technique / this OCSF
class / these first/last-seen timestamps." Their revival hinges on the §2 placement decision:

- **If the MISP connector is a runner plugin** writing through `PluginResult`, then intel sync
  would naturally want `stix_refs`/`attack_refs`/`first_seen_at`/`last_seen_at` populated —
  and STIX interchange would be the thing emitting them. That's the case for reviving them.
- **But §2 recommends the connector is _not_ a runner plugin** — it's an in-API service writing
  Alerts/Observables directly on the dedup key. Intel import therefore does **not** flow through
  `PluginResult` at all. Nothing in the recommended §5.1 build emits these columns.

Furthermore, the columns' semantics belong to **entity enrichment**, and where intel provenance
_does_ have a natural home in the recommended design it's on the **Alert/Observable** (source,
source_ref, external_link, tags), not on `PluginResult`. STIX interchange (§1) is an
edge-format concern, consumed as dicts into `NormalizedEvent` — it doesn't produce
`PluginResult` rows.

**Recommendation: keep the deferred columns DEAD for Phase 5. STIX interchange does NOT revive
them.** Reasons:

1. The recommended connector placement (§2) doesn't route intel through `PluginResult`, so §5.1
   gives them no emitter.
2. STIX-as-interchange (§1) is a parse-front-end/edge concern, not a plugin-enrichment output;
   it produces alerts/observables, not `PluginResult` rows.
3. The existing rule — "deferred until a **real plugin** emits them; `normalized` JSON carries
   them meanwhile" — is the right trigger and it hasn't fired. The correct thing to revive these
   is an **enrichment plugin that natively produces STIX/OCSF/ATT&CK-tagged output** (e.g. a
   future threat-intel _analyzer_, a §5.4-adjacent enrichment plugin), not the MISP _connector_.
4. Promoting them now = a migration + index cost for columns with no writer and no reader,
   which is precisely what the deferral was designed to avoid.

**Keep them deferred; re-evaluate when the first enrichment plugin actually emits STIX/ATT&CK
structure — a §5.4/analyzer trigger, not a §5.1/connector trigger.** If the owner later wants
ATT&CK linkage on _imported_ intel, the cheaper path is tags/pattern-links on the Alert (we
already have the MITRE pattern catalog), not resurrecting `PluginResult.attack_refs`.

---

## Open decisions for owner

Each maps to a section above. Recommendation in **bold**; the owner decides.

1. **Interchange model (§1).** Adopt the thin `IntelSource` seam + Catlico-shaped
   `NormalizedEvent` DTO; **do NOT** make STIX 2.1 the universal internal bus (STIX stays an
   edge format, consumed as dicts). → **Accept Option C?**
2. **Connector placement (§2).** Interactive legs (`test`/`import-now`/`export-case`) **in-API**
   filling the existing 501s; scheduled sync as an **in-API poller** in OSS, accepting the
   documented single-instance limit; runner-plugin path reserved as a Phase-6/HA migration
   target, not built now. → **Accept the hybrid, in-API-first?**
3. **Sync-policy schema (§3).** Add a validated `MispSyncPolicy` over `MispServer.config`
   (purpose / import filters / export flags / attribute type map), validated on CRUD write.
   → **Accept the shape?** Sub-decision: **attribute type map = org-default + per-server
   override** (recommended) vs strictly per-server.
4. **Library (§4).** **Hand-rolled `httpx` `MispClient`** with fetch/parse split (matching
   `attack_import`); no `pymisp`, no `stix2`. → **Accept?**
5. **Sightings & export (§5).** Sightings **OUT of scope** for OSS MISP (defer to enterprise
   fabric); export = IOC-only by default (`ioc_only`↔`Observable.ioc`, confirmed) with the
   **mandatory write-back linking alert**. → **Accept both?**
6. **Enterprise boundary (§6).** Basic MISP (connector + policy + the `IntelSource` seam) =
   **OSS**; OpenCTI + STIX interchange + cross-org/sightings sync = **Enterprise**, plugged in
   via the existing `catlico.extensions` seam. → **Confirm the line?**
7. **Deferred `PluginResult` columns (§7).** Keep `schema_refs`/`stix_refs`/`ocsf_class`/
   `attack_refs`/`severity`/`first_seen_at`/`last_seen_at` **DEAD**; STIX interchange does not
   revive them; revisit only when a real enrichment plugin emits them. → **Confirm they stay
   deferred?**

**Gate exit:** once 1–7 are answered, §5.1 (MISP end-to-end) and §5.2 (OpenCTI/STIX, enterprise)
are unblocked, and the Phase 0 MISP UI controls can be un-hidden.
