# new-hive — Fine-Grained RBAC Design (what I want)

> A forward design spec for **our** product (not reverse-engineered TheHive). Goal: replace
> TheHive's coarse permission model (22 bundled `manageCase`/`manageObservable`/… perms) with a
> **fine-grained `verb:resource`** model — `read:cases`, `write:cases`, `delete:cases`, … — so an
> org can grant exactly what it means and, e.g., give analysts read+write on cases but **not**
> delete (community ask [#2241](https://github.com/TheHive-Project/TheHive/issues/2241)).
>
> Non-negotiable: this slots into the **two-level tenancy gate** (a case is visible only via a
> Share, and the effective permission is *membership-profile* ∩ *case-share-profile*) — see
> [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md) and the proven
> predicates in [`poc-postgres/`](./poc-postgres/). Fine-grained perms change the *vocabulary*,
> not the gate. Implementation home: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md)
> M2; rationale: [`thehive5-design-improvements.md`](./thehive5-design-improvements.md) B3.

---

## 1. The model in one screen

A **permission** is a string `action:resource[:scope]` (lowercase, plural resource).

```
read:cases            # read any visible case
write:cases           # create + update cases   (sugar; see §3)
delete:cases          # delete cases            (always separate — the #2241 ask)
assign:cases          # (re)assign a case
close:cases           # resolve/close a case
merge:cases           # merge cases / alerts into a case
share:cases           # share a case to another org
export:cases          # export / report a case
update:cases:own      # update only cases I'm assigned to (optional scope, §5)
manage:users          # admin sugar = *:users
*:*                   # platform superadmin
```

- **Resources** = the things you act on (data plane + admin plane, §2).
- **Actions** = atomic verbs (`read/create/update/delete` + resource-specific action verbs), with
  three **sugar** verbs (`write`, `manage`, `*`) that expand to atomic verbs at load time (§3).
- **Scope** (optional) = `:own` vs `:any` (default `:any`) for row-level narrowing (§5).
- **Profiles** = named bundles of permissions, assigned per-org via membership (§6).
- **Enforcement** = a Level-1 check (`require_perm`) on the caller's membership profile **and** a
  Level-2 check (`require_can`) on the case's share profile (§7).

---

## 2. Resource catalog

**Data plane** (case-scoped or org-scoped records):

| Resource | Notes |
|---|---|
| `cases` | the central record |
| `tasks` | belong to a case |
| `task-logs` | the work log entries on a task |
| `observables` | IOCs/artifacts on a case or alert |
| `alerts` | inbound events; promote→case |
| `comments` | free-text on cases/alerts (new in our build) |
| `attachments` | files on logs/observables/cases |
| `procedures` | MITRE ATT&CK procedures on a case |
| `dashboards` | org-shared or personal |
| `pages` | knowledge-base pages |

**Admin plane** (org configuration / platform):

| Resource | Scope | Notes |
|---|---|---|
| `users` | org | membership of *this* org |
| `profiles` | org/platform | permission bundles |
| `custom-fields` | org | definitions |
| `observable-types` | org | |
| `taxonomies` / `tags` | org | |
| `patterns` | platform | MITRE catalog import |
| `analyzer-templates` | org | Cortex report templates |
| `functions` | org | automation engine (M10) |
| `metrics` | org | KPI definitions |
| `report-templates` | org | case-report widgets (M10) |
| `config` | org | runtime config store |
| `organisations` | platform | create/manage orgs + org-links |
| `platform` | platform | server-wide settings, maintenance |

---

## 3. Verbs — atomic vs sugar

**Atomic verbs** (what the gate actually checks):

| Verb | Meaning | Applies to |
|---|---|---|
| `read` | view / list / query | all |
| `create` | create new | all |
| `update` | modify existing | all |
| `delete` | remove | all |
| `assign` | (re)assign owner/assignee | cases, tasks |
| `close` | resolve/close/complete | cases, tasks |
| `merge` | merge cases; bulk-merge alerts | cases, alerts |
| `share` | share to another org | cases |
| `export` | export/report out | cases, observables |
| `import` | bulk import | alerts, observables, taxonomies, patterns |
| `run` | execute | analyzers, responders, functions |
| `bulk` | run a bulk async job (§ plan B4) | cases, alerts, observables |

**Sugar verbs** (expanded to atomic at load time — never stored as the effective grant):

| Sugar | Expands to | Example |
|---|---|---|
| `write` | `create` + `update` | `write:cases` ⇒ `create:cases`, `update:cases` |
| `manage` | `create`+`update`+`delete` (+ all action verbs for that resource) | `manage:users` ⇒ all user verbs |
| `*` | every atomic verb valid for that resource | `*:cases` |

So `read:cases` + `write:cases` (your example) grants list/view + create/update, **without**
`delete:cases` — exactly the granularity TheHive lacked. `delete` is **never** implied by `write`
or `manage`-on-another-resource; it must be granted explicitly.

> **Implication rule:** there are **no hidden implications** except that `write`/`update`/`delete`/
> action-verbs on a resource implicitly require `read` on that resource (you can't act on what you
> can't see) — enforced by auto-adding `read:<resource>` when any non-read verb is present. This is
> the one convenience implication; everything else is explicit.

---

## 4. Permission grammar

```
permission   := action ":" resource [ ":" scope ]
action       := "read" | "create" | "update" | "delete"
              | "assign" | "close" | "merge" | "share" | "export" | "import" | "run" | "bulk"
              | "write" | "manage" | "*"          ; sugar
resource     := "cases" | "tasks" | "task-logs" | "observables" | "alerts" | "comments"
              | "attachments" | "procedures" | "dashboards" | "pages"
              | "users" | "profiles" | "custom-fields" | "observable-types" | "taxonomies"
              | "tags" | "patterns" | "analyzer-templates" | "functions" | "metrics"
              | "report-templates" | "config" | "organisations" | "platform" | "*"
scope        := "own" | "any"                     ; default "any" if omitted
```

Validation rejects unknown action/resource tokens and action/resource pairs that don't make sense
(e.g. `merge:dashboards`). The valid (action × resource) matrix is declared in code and exposed via
`GET /permission` and `/describe` so the UI and SDK can render permission pickers.

---

## 5. Scopes — light ABAC (optional)

Most permissions are org-wide (`:any`, the default). For the common "analysts may edit only their
own cases" pattern we support a single, cheap row-scope:

- `update:cases:own` / `close:cases:own` / `read:cases:own` — granted only when the caller is the
  case **assignee** or **creator**. `:own` for tasks = caller is the task assignee.
- `:any` (default) = any case visible to the org.

**Recommended v1:** support `:own`/`:any` for `cases` and `tasks` only; everything else is `:any`.

**Deferred (documented, not v1):** richer conditions as *profile attributes* (not permission
strings) — e.g. `max_tlp: 2` on a profile caps what an analyst can read/act on; `tag_filter` limits
to certain case tags. These compose with the gate as extra `WHERE` predicates. Kept out of the core
string grammar to keep permissions readable.

---

## 6. Built-in profiles (presets composed from fine-grained perms)

Profiles are editable bundles; these ship as defaults. Custom profiles compose any valid perms.

```yaml
read-only:
  - read:*                       # data plane read; no admin

analyst:
  - read:*                       # see everything visible to the org
  - write:cases                  # create+update (NOT delete)
  - write:tasks
  - write:task-logs
  - write:observables
  - write:alerts
  - write:comments
  - assign:cases
  - assign:tasks
  - close:tasks
  - share:cases
  - run:analyzers
  - run:responders
  - create:dashboards            # personal dashboards

senior-analyst:                  # analyst + destructive/curational verbs
  - <all analyst perms>
  - delete:cases
  - delete:tasks
  - delete:observables
  - delete:alerts
  - delete:comments
  - close:cases
  - merge:cases
  - import:alerts
  - export:cases
  - bulk:cases
  - write:dashboards

org-admin:                       # everything in the org, incl. org config
  - manage:cases
  - manage:tasks
  - manage:task-logs
  - manage:observables
  - manage:alerts
  - manage:comments
  - manage:dashboards
  - manage:pages
  - manage:procedures
  - manage:users                 # of THIS org
  - manage:profiles
  - manage:custom-fields
  - manage:observable-types
  - manage:taxonomies
  - manage:analyzer-templates
  - manage:functions
  - manage:metrics
  - manage:report-templates
  - manage:config

platform-admin:                  # only effective in the admin org (§7)
  - "*:*"                         # incl. manage:organisations, manage:platform, manage:patterns
```

**The #2241 ask, expressed directly:** the default `analyst` already lacks `delete:*`. To take
delete away from an existing custom profile, drop the `delete:<resource>` perms — no code change.

---

## 7. Enforcement (maps to the FastAPI/SQLAlchemy build)

**Two levels, both required** (the tenancy core — unchanged in shape, finer in vocabulary):

1. **Level-1 — membership profile.** The caller's profile *in the current org* must grant the
   permission. FastAPI dependency: `require_perm("update:cases")`. Backed by `AuthContext.permissions`
   (the expanded, atomic, scope-resolved frozenset loaded once per request).
2. **Level-2 — case-share profile.** For a case-scoped action, the **Share that makes the case
   visible to this org** must *also* grant the permission: `require_can(ctx, case_id, "update:cases")`.
   This is what lets Org A share a case to Org B **read-only** (the share carries a profile with
   only `read:*`) or **collaborative** (a share profile with `write:tasks`, `write:comments`).

Both gates AND with **visibility** (a Share to the org must exist). Expressed as the two-level
`.can()` from the PoC (`V5`) — Level-1 from membership, Level-2 from the share profile.

**Resolution pipeline (per request):**
```
profile perms (strings)
  → expand sugar (write→create,update; manage→…; *→all-valid)
  → add implied read:<resource> for any non-read verb
  → resolve scopes (:own needs row context: assignee/creator)
  → AuthContext.permissions: frozenset[str]   (atomic, e.g. {"read:cases","update:cases", ...})
```

**Storage:** `profile_permission(profile_id, permission_string)` rows. Share carries a
`share_profile` (already in the model). Admin-plane perms (`*:organisations`, `*:platform`,
`manage:patterns`) are **only effective in the admin org** — carried over from TheHive's
`restrictedPermissions` concept; assigning them in a normal org is a no-op (and the UI hides them).

**Where the checks live (per the plan §4.1/§4.3A):** `core/permissions.py` (catalog + expansion),
`core/auth_context.py` (the frozenset), `core/tenancy.py` (`require_perm` Level-1, `require_can`
Level-2), `api/deps.py` (the FastAPI dependencies). A **gate-coverage CI test** asserts every
mutating route declares a `require_perm`/`require_can`.

---

## 8. Migration from TheHive's 22 permissions

Existing TheHive profiles import via this map (so a TH4/TH5 export lands on sane defaults):

| TheHive perm | new-hive perms |
|---|---|
| `manageCase` | `create/update/delete/close/merge:cases` |
| `manageTask` | `manage:tasks`, `manage:task-logs` |
| `manageObservable` | `manage:observables` |
| `manageAlert` | `manage:alerts`, `import:alerts`, `merge:alerts` |
| `manageShare` | `share:cases` |
| `manageProcedure` | `manage:procedures` |
| `managePage` | `manage:pages` |
| `manageTag` | `manage:taxonomies`, `manage:tags` |
| `manageCaseTemplate` | `manage:case-templates` |
| `manageAnalyse` | `run:analyzers` |
| `manageAction` | `run:responders` |
| `accessTheHiveFS` | *(dropped — WebDAV/TheHiveFS not carried)* |
| `manageUser` | `manage:users` |
| `manageConfig` | `manage:config` |
| `manageOrganisation` | `manage:organisations` *(admin org)* |
| `manageProfile` | `manage:profiles` |
| `manageCustomField` | `manage:custom-fields` |
| `manageObservableTemplate` | `manage:observable-types` |
| `manageTaxonomy` | `manage:taxonomies` |
| `managePattern` | `manage:patterns` *(admin org)* |
| `manageAnalyzerTemplate` | `manage:analyzer-templates` |
| `managePlatform` | `manage:platform` *(admin org)* |

The import expands these and then offers to "tighten" (e.g. split `manageCase`→ drop `delete:cases`)
in a post-import review.

---

## 9. Examples

**A read-only auditor in Org A:** profile `{read:*}` → can list/view every visible case but cannot
create/modify/delete anything; the live stream shows them activity (gated by visibility).

**An analyst who can edit but not delete (the #2241 case):**
`{read:*, write:cases, write:tasks, write:task-logs, write:observables, assign:cases, close:tasks}`
→ full day-to-day work, zero delete.

**Share a case from Org A to Org B, read-only:** create the Share with a share profile `{read:*}`.
Org B users (even org-admins) get only `read` on that case via Level-2, regardless of their own
membership perms.

**Share for collaboration:** share profile `{read:*, write:tasks, write:task-logs, write:comments}`
→ Org B can work the tasks but cannot delete the case or re-share it.

**"Own cases only" analyst:** `{read:cases, update:cases:own, close:cases:own, write:task-logs:own}`
→ can edit only cases assigned to them.

---

## 10. API surface

- `GET /permission` — the full catalog (valid action×resource matrix, sugar expansions, scopes) for
  UI permission pickers + SDK.
- `GET /profile`, `POST /profile`, `PATCH /profile/{id}`, `DELETE /profile/{id}` — manage bundles
  (gated by `manage:profiles`).
- Membership assigns a profile per `(user, org)`.
- Shares carry a `profile` (the Level-2 grant).
- All of the above appear in **OpenAPI** and `/describe` automatically (plan A4/M3).

---

## 11. Decisions to confirm (the knobs)

1. **Verb granularity** — I went with atomic `read/create/update/delete` + action verbs, plus sugar
   `write`(=create+update) / `manage` / `*`. *Alternative:* only `read`/`write`/`delete` (coarser).
   **Recommend the atomic+sugar model** as written — it gives `read:cases write:cases` *and* the
   fine split when needed.
2. **Row scopes (`:own`/`:any`)** — I propose supporting `:own` for `cases`/`tasks` only in v1.
   *Alternatives:* none (org-wide only), or everywhere. **Recommend cases/tasks-only.**
3. **ABAC conditions (`max_tlp`, tag filters)** — proposed as **profile attributes**, deferred past
   v1. Confirm whether TLP-ceiling enforcement is needed early.
4. **`manage` semantics** — does `manage:cases` include `share`/`merge`/`export`/`bulk`, or just
   CRUD? I've defined `manage` = CRUD + all action verbs for that resource. Confirm if you want a
   tighter `manage`.

---

## Cross-references
- Tenancy gate & sharing mechanics: [`thehive-rbac-sharing-mechanisms.md`](./thehive-rbac-sharing-mechanisms.md), [`poc-postgres/`](./poc-postgres/)
- Where it builds: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (M2)
- Why finer RBAC: [`thehive5-design-improvements.md`](./thehive5-design-improvements.md) (B3) + [`thehive5-community-requested-features.md`](./thehive5-community-requested-features.md) (#2241, #2259)
- Pitfalls to avoid: [`thehive4-pitfalls-and-gotchas.md`](./thehive4-pitfalls-and-gotchas.md) (§1 isolation, §8 auth)
