# PoC: TheHive 4 multi-tenancy on PostgreSQL + Row-Level Security

A small, **runnable** proof that the hardest correctness requirement of a TheHive
rewrite — the org-scoped `Share` visibility model (see
[`../thehive4-parity-spec.md`](../thehive4-parity-spec.md) §3 and
[`../thehive-api-internals.md`](../thehive-api-internals.md) §6) — can be enforced
**by the database** using Postgres Row-Level Security, instead of by app code
that might forget a `WHERE` clause.

This is the relational equivalent of TheHive's `.visible` / `.can` traversal steps.

## What's here

```
migrations/
  V1__core_rbac.sql                 organisation, profile, app_user, membership + session-org helpers
  V2__cases_shares_observables.sql  case_, task, observable + the Share fan-out
  V3__row_level_security.sql        the RLS ORG-VISIBILITY gate (what you can see)
  V4__app_role.sql                  the non-superuser app role (RLS only applies to non-superusers!)
  V5__permissions.sql               the RLS PERMISSION gate on writes (the two-level `.can()`)
seed.sql                            2 orgs, 3 cases (1 private each + 1 shared A→B read-only)
demo.sql                            proves org isolation + sharing (read visibility)
demo_permissions.sql                proves the two-level write-permission model
```

## Run it (Postgres 14+)

```bash
createdb thehive_poc
psql -d thehive_poc -f migrations/V1__core_rbac.sql
psql -d thehive_poc -f migrations/V2__cases_shares_observables.sql
psql -d thehive_poc -f migrations/V3__row_level_security.sql
psql -d thehive_poc -f migrations/V4__app_role.sql
psql -d thehive_poc -f seed.sql
psql -d thehive_poc -f demo.sql
```

The app connects as a normal role and sets its "current organisation" per session:

```sql
SET ROLE thehive_app;              -- NOT a superuser (superusers bypass RLS)
SELECT set_current_org('org-a');   -- the AuthContext org
SELECT * FROM case_;               -- transparently filtered to org-a's visible cases
```

## How the gate works

| Table | Policy (`USING`) |
|---|---|
| `case_share` | `organisation_id = current_org()` — an org sees only its own shares |
| `case_` | `owning_organisation_id = current_org() OR id IN (SELECT case_id FROM case_share)` |
| `task` / `observable` | `case_id IN (SELECT id FROM case_)` — cascades the case rule down |

The policies **compose**: the `case_share` subquery is itself RLS-filtered, so the
`case_` rule reads as "cases I own, or that someone shared with me", and
task/observable inherit visibility from their case. `current_org()` reads a
per-session GUC (`app.current_org_id`) set by `set_current_org()`.

## Verified output

Applying the migrations + seed + demo against PostgreSQL 16 produces:

```
================ As org-a (Alice) ================
-- cases visible to org-a (expect #1 owned + #3 shared) --
 number |          title          | owning_organisation_id
      1 | A-only phishing wave    |                      1
      3 | Joint APT investigation |                      1     (2 rows)

================ As org-b (Bob) ==================
-- cases visible to org-b (expect #2 owned + #3 shared-in) --
 number |          title           | owning_organisation_id
      2 | B-only ransomware triage |                      2
      3 | Joint APT investigation  |                      1     (2 rows)

== org-b CANNOT see case #1 even by primary key ==   should_be_zero = 0
== org-b updating case #1 affects ==                 rows_updated   = 0
== No org context set => sees nothing ==             visible_cases  = 0
```

i.e. **org A and org B each see only their own private case plus the one shared
case #3 — never each other's private data — and the gate applies to writes and
direct primary-key access too.**

## The gotcha this PoC surfaced (worth remembering)

RLS is **bypassed by superusers and by `BYPASSRLS` roles**, regardless of
`FORCE ROW LEVEL SECURITY`. The first run of this demo (accidentally executed as
the `postgres` superuser) showed *all* cases to *everyone* — the policies looked
broken but were simply being skipped. Always run the app as an ordinary role
(`thehive_app` here) and verify with a non-superuser; otherwise your tenancy gate
is silently inert.

## The permission gate (V5) — the two-level `.can()`

V3 answers *"which rows can I see?"*. V5 answers *"may I write this one?"* —
reproducing TheHive's two-level `.can(permission)`:

1. the user's profile **in the current org** must hold the permission, **and**
2. the profile on the **case's share to that org** must hold it too.

Implemented as `RESTRICTIVE` RLS policies (which AND with the V3 visibility
policies) backed by `has_perm()` (level 1) and `can_case()` (levels 1 + 2). Run
the demo after the migrations + seed:

```bash
psql -d thehive_poc -f migrations/V5__permissions.sql
psql -d thehive_poc -f demo_permissions.sql
```

Verified output (PostgreSQL 16):

```
-- Alice (analyst, org-a owner share) updates case #3        => rows_updated = 1
-- Bob (analyst in org-b; #3 shared to org-b READ-ONLY)      => rows_updated = 0   (blocked!)
-- Bob can still READ case #3                                => sees "...[edited by A]"
-- Bob updates his OWN case #2 (org-b owner share = analyst) => rows_updated = 1
```

i.e. **an analyst in org B cannot modify a case that was shared to org B
read-only — even though his org role *has* `manageCase` — because the share
profile gates it. He can still read it, and edit cases he owns.** That two-level
check is exactly TheHive's `if (authContext.permissions.contains(p)) … share.profile.has(p)`.

## Scope / not included

This PoC covers the **org boundary** (V3) and the **two-level write-permission
gate** (V5). A full implementation would extend the permission policies to the
remaining entities/operations and add the rest of the tables/indexes from the
parity spec. Full-text search would go to OpenSearch/Elasticsearch, not Postgres.
