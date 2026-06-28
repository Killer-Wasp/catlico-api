# new-hive — Pluggable Observable Enrichment (analyzer plugins)

> What I want: the Cortex idea — **check/enrich an observable against OSINT & SOC tools** — but as a
> first-class, **add/remove plugin** system native to new-hive. Each plugin inspects an observable
> (an IP, domain, hash, email…) and returns a verdict + enrichment. Examples: a **geoip2** plugin, a
> **crowdstrike** plugin, a **misp** plugin, VirusTotal, AbuseIPDB, etc.
>
> Design goals: (1) drop-in **plugins you install/enable/disable per org**; (2) **lightweight** —
> no separate Scala service or Cassandra; (3) **secure-by-default** (TLP/PAP gating, egress control,
> audited, sandboxed when untrusted); (4) **Cortex-compatible** so the existing analyzer catalog
> isn't stranded. Builds on the plugin-extension-point idea in
> [`thehive5-design-improvements.md`](./thehive5-design-improvements.md) (B6) and the Cortex
> mechanics in [`cortex.md`](./cortex.md); implementation home: M7 of
> [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md).

---

## 1. Concept & how it differs from Cortex

**Cortex today:** a separate Scala/Play service that runs Python "analyzer" workers via
process/docker/k8s, talks to TheHive over REST, and stores jobs in Elasticsearch. Powerful, but
**heavy to operate** (the very ops weight users complained about).

**new-hive enrichment plugins:** a plugin is a **Python package** implementing a small contract,
**discovered via entry points**, configured per-org, and run by new-hive's own worker pool. Trusted
plugins run in-process; untrusted ones run sandboxed. No external orchestrator required.

| | Cortex | new-hive plugins |
|---|---|---|
| Packaging | analyzer repo + flavors | pip package (internal registry) or `plugins/` dir |
| Discovery | Cortex catalog | Python entry points `newhive.plugins` + a registry table |
| Runtime | separate Scala service + workers | new-hive worker pool (in-proc / sandboxed) |
| Add/remove | redeploy Cortex | install/uninstall + enable/disable per org (API) |
| Results | Job + report taxonomies | EnrichmentReport → verdict badges + artifacts + audited ops |
| Ops weight | Cassandra/ES + Cortex | none extra (uses our PG/SQLite + workers) |

**We keep Cortex working anyway** (§11): a built-in **`cortex-proxy` plugin** calls an existing
Cortex server, and a **cortexutils-compatible runner** lets existing analyzer scripts run as
plugins with little/no change — so nobody loses their catalog.

---

## 2. The plugin contract

Two pieces: a **manifest** (declares what it is, what it needs) and a **`check()`** entrypoint.

```python
# new-hive plugin contract (Pythonic, typed)

class Observable:
    data_type: str        # "ip" | "domain" | "fqdn" | "url" | "hash" | "email" | "file" | ...
    data: str
    tlp: int              # 0..3
    pap: int              # 0..3

class Verdict(Enum):       # the badge level
    INFO; SAFE; SUSPICIOUS; MALICIOUS

class Taxonomy:            # short badge shown on the observable
    namespace: str; predicate: str; value: str; level: Verdict

class Operation:           # optional mutations, applied through audited services (§6)
    kind: str              # "add_tag" | "set_severity" | "add_observable" | "create_task" | "comment"
    params: dict

class EnrichmentReport:
    verdict: Verdict
    taxonomies: list[Taxonomy]      # e.g. GeoIP:country="AU"; CrowdStrike:malicious=true
    artifacts: list[Observable]     # new observables to pivot on (deduped on import)
    operations: list[Operation]     # optional case/observable mutations
    full: dict                      # raw provider response (stored, not indexed by default)

class EnrichmentPlugin(Protocol):
    manifest: PluginManifest
    def check(self, observable: Observable, config: PluginConfig) -> EnrichmentReport: ...
    # optional: def healthcheck(self, config) -> bool
```

This is intentionally the **same shape as the cortexutils contract** (verdict/taxonomies/artifacts/
operations/full) so the mental model and existing analyzers carry over.

**Manifest** (ships with the plugin; validates config; declares security posture):

```toml
[plugin]
name         = "geoip2"
version      = "1.0.0"
display_name = "MaxMind GeoIP2"
data_types   = ["ip"]            # engine only runs it on these observable types
needs_egress = false             # local DB lookup → no network
trust        = "first-party"     # first-party | trusted | untrusted  (drives sandboxing, §8)

[plugin.config]                  # validated per-org config schema
db_path = { type = "path", required = true }

[plugin.guardrails]
max_tlp = 3                      # safe even on RED (no data leaves the host)
max_pap = 3
timeout_s = 5
```

---

## 3. Plugin types, examples & dataType routing

**v1 type = Enricher/Analyzer** (read-an-observable → verdict + enrichment). (Responder/"action"
plugins — block an IP, push to firewall — are a later type reusing the same contract + `operations`.)

The engine **routes by `data_type`**: only plugins whose manifest lists the observable's type run.

| Plugin | data_types | egress | needs secrets | what it returns |
|---|---|---|---|---|
| **geoip2** | `ip` | no (local DB) | no | country/ASN/city taxonomies; verdict=INFO |
| **crowdstrike** | `hash`,`domain`,`ip` | `api.crowdstrike.com` | client id/secret | detection verdict + IOC context + artifacts |
| **misp** | all | MISP server | API key | sightings/known-IOC verdict; tags; related events as artifacts |
| virustotal | `hash`,`url`,`domain`,`ip` | `www.virustotal.com` | api key | detection ratio → verdict; artifacts |
| abuseipdb | `ip` | `api.abuseipdb.com` | api key | abuse score → verdict |
| internal-soc-lookup | any | internal API | token | hits in your SIEM/asset DB |

---

## 4. Lifecycle & management (add / remove / enable / configure)

```
install   → pip install from the INTERNAL registry (org policy, §8) OR drop into plugins/ dir
discover  → Python entry point group `newhive.plugins` → engine reads manifests → `plugin` registry rows
enable    → per-org toggle (a plugin can be installed platform-wide but enabled per org)
configure → per-org PluginConfig validated against manifest [plugin.config]/[plugin.secrets]
run       → manual / auto / scheduled (§5)
disable   → per-org off switch (keeps config)
remove    → uninstall package / delete from dir → registry marks unavailable
```

- **Admin API:** `GET /plugin` (catalog + manifests), `POST /plugin/{name}/enable|disable`,
  `PUT /plugin/{name}/config` (per-org, secrets write-only), `POST /plugin/{name}/healthcheck`.
  Gated by `manage:analyzer-templates` / a new `manage:plugins` perm (see RBAC doc).
- **UI:** a plugin catalog page — toggle, configure keys, see supported dataTypes & health.

---

## 5. Execution model

**Triggers:**
- **Manual** — analyst clicks "enrich" on an observable / runs a chosen plugin.
- **Auto-on-create** — run the enabled enrichers automatically when an observable is added
  (the community ask [#261](https://github.com/TheHive-Project/TheHive/issues/261)); also on
  **alert pre-processing** before promotion.
- **Scheduled re-check** — re-enrich stale observables (e.g. nightly) via the scheduler.
- **From a Function** — the automation engine (M10) can invoke a plugin.

**Routing & running:** the engine filters enabled plugins by `data_type`, checks guardrails (§8),
then dispatches each as an **async job in the worker pool** (so a slow external API never blocks the
request). Trusted plugins run in-process; **untrusted** ones run **sandboxed** (subprocess/container,
egress-restricted). Each job has a timeout and resource cap from the manifest.

**Caching:** results cache on `(plugin, version, data_type, data)` with a per-plugin TTL — avoids
hammering paid APIs. Expose **force-refresh** and surface "result is cached/age" so stale intel
isn't mistaken for fresh (the Cortex cache-staleness gotcha, pitfalls §6 #36).

---

## 6. Results & mutations

An `EnrichmentReport` is stored as a **job attached to the observable** and rendered as:
- **Verdict badges** (taxonomies) on the observable — e.g. `GeoIP:country="AU"`,
  `CrowdStrike:malicious`, `MISP:sightings=3`. The worst verdict can roll up to the observable.
- **Artifacts** → new observables added to the case, **deduped** on `(data_type, data)` (avoids the
  "observable already exists" race, pitfalls §6 #32).
- **Operations** → optional mutations (add tag, set severity, create task, comment) **applied
  through the normal audited services as a scoped service principal** — so unlike Cortex (where
  responder ops are *not* audited, pitfalls §6 #30), **every plugin-driven change is in the audit
  trail and the timeline** ("geoip2 plugin added tag …").

---

## 7. Per-org config & secrets

- Config is validated against the manifest schema on save; bad config is rejected with field errors.
- **Secrets** (API keys, client secrets) are stored via a secrets backend (vault/KMS), referenced by
  id in `plugin_config`, **never returned by the API** and **redacted from logs**.
- Config is **per-org**: Org A and Org B can point the same `misp` plugin at different servers/keys.

---

## 8. Security — secure-by-default (this is third-party code running on your data)

Plugins are an obvious supply-chain + SSRF + data-exfil surface. Defaults:

1. **Install only from the approved internal registry.** Per org policy, third-party plugin packages
   come from the internal Nexus PyPI proxy (`shared-pypi-proxy`), **never pypi.org**. Plugins can be
   pinned + checksum-verified; a `trust` level in the manifest gates how they run.
2. **TLP/PAP gating before any external call.** A plugin with `needs_egress=true` and `max_tlp=2`
   will **not** run on a RED (tlp=3) observable — enforced by the engine *before* dispatch (pitfalls
   §6 #33). Local plugins (geoip2) can run at any TLP.
3. **Egress allowlist per plugin.** A plugin may only reach the hosts in its manifest
   `egress_allow`; the sandbox blocks everything else, incl. RFC1918 / `169.254.169.254` (SSRF).
4. **Sandbox untrusted plugins.** `trust="untrusted"` ⇒ run in a subprocess/container with no host
   FS, dropped capabilities, CPU/mem/wall-clock caps. First-party plugins may run in-process.
5. **Scoped principal + full audit.** Plugin runs and their `operations` execute as a scoped service
   identity through the gated/audited path — same contract as connectors (plan M7).
6. **No secret leakage.** Secrets resolved at run, redacted from reports/logs.

---

## 9. Data model (PG / SQLite, dialect-portable)

```
plugin            (name PK, version, manifest JSON, data_types, needs_egress, trust, available)
plugin_config     (org_id, plugin_name, enabled, settings JSON, secrets_ref, updated_*)   UNIQUE(org_id, plugin_name)
enrichment_job    (id, org_id, observable_id, plugin_name, plugin_version, status,
                   verdict, report JSON, cache_key, from_cache, started_at, ended_at, error)
                   -- status: queued|running|success|failure ; cache_key = hash(plugin,version,dtype,data)
report_tag        (observable_id, namespace, predicate, value, level)   -- the verdict badges
```

Artifacts created by a job are normal `observable` rows linked to the case (deduped); operations are
applied via services (no special table). `enrichment_job` reuses the connector/job machinery from
M7 so Cortex jobs and native-plugin jobs share one model.

---

## 10. API surface

- `GET /plugin` / `GET /plugin/{name}` — catalog + manifest (+ per-org enabled/config status).
- `PUT /plugin/{name}/config`, `POST /plugin/{name}/enable|disable|healthcheck` — management.
- `POST /observable/{id}/enrich` — run all matching enabled plugins (or `?plugin=geoip2` for one).
- `POST /alert/{id}/enrich` — alert pre-processing.
- `GET /observable/{id}/enrichments` — jobs + verdicts for an observable.
- All in OpenAPI + `/describe` (plan A4/M3). Config writes gated by `manage:plugins`; running gated
  by `run:analyzers` (see [`newhive-rbac-design.md`](./newhive-rbac-design.md)).

---

## 11. Cortex compatibility / migration (don't strand the ecosystem)

- **`cortex-proxy` plugin** — a built-in plugin whose `check()` calls a configured Cortex server's
  analyzer and maps the report back. Lets you keep using a Cortex install through the new UI.
- **cortexutils-compatible runner** — run an existing Cortex analyzer script (its `run()` emitting
  `report`/`taxonomies`/`artifacts`/`operations`) as a sandboxed plugin with a thin shim, so the
  large community analyzer catalog works with minimal porting.
- Migration: existing Cortex analyzer configs map to `plugin_config` rows.

---

## 12. Example plugins (concrete)

**geoip2** (local, no egress, any TLP):
`check(ip)` → looks up MaxMind DB → `EnrichmentReport(verdict=INFO,
taxonomies=[GeoIP:country=AU, GeoIP:asn=AS1234], full={...})`. No artifacts.

**crowdstrike** (egress to `api.crowdstrike.com`, max_tlp=2, secrets=client id/secret):
`check(hash)` → OAuth → query Falcon → if known-bad
`verdict=MALICIOUS, taxonomies=[CrowdStrike:detection=malware], artifacts=[related domains/ips],
operations=[set_severity(high), add_tag("crowdstrike:malicious")]`.

**misp** (egress to MISP server, secrets=api key, all dataTypes):
`check(obs)` → search attributes → if sighted
`verdict=SUSPICIOUS, taxonomies=[MISP:sightings=3], artifacts=[attributes from the event],
operations=[add_tag("misp:event:1234")]`.

---

## 13. Decisions to confirm

1. **Runtime default** — I propose **in-process for first-party/trusted** plugins and **sandboxed
   subprocess/container for untrusted**. Alternative: sandbox *everything* (safer, heavier). Confirm
   the default trust posture.
2. **Plugin language** — Python-only contract (simplest, matches the catalog), or also allow a
   generic "container plugin" (any language, stdin/stdout JSON like cortexutils)? **Recommend
   Python-first + an optional container runner** (covers both).
3. **Auto-run scope** — run *all* enabled enrichers on observable creation, or a per-org "auto-run
   set"? **Recommend a per-org auto-run allowlist** (cost/rate control).
4. **Plugin permission** — add a dedicated `manage:plugins` + `run:analyzers` (RBAC doc), or fold
   plugin management under `manage:analyzer-templates`? **Recommend a dedicated `manage:plugins`.**

---

## Cross-references
- Cortex mechanics this generalizes: [`cortex.md`](./cortex.md); MISP: [`thehive-misp-connector.md`](./thehive-misp-connector.md)
- Extension-point philosophy: [`thehive5-design-improvements.md`](./thehive5-design-improvements.md) (B6, B7)
- Permissions for running/managing: [`newhive-rbac-design.md`](./newhive-rbac-design.md)
- Security gotchas designed around: [`thehive4-pitfalls-and-gotchas.md`](./thehive4-pitfalls-and-gotchas.md) (§6 connectors: #30 audit, #32 dedup race, #33 TLP-before-call, #36 cache staleness)
- Build home: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (M7)
