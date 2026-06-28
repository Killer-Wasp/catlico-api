# RBAC & Sharing — operation-level mechanisms

> Fine-grained mechanism for the **role / membership** and **case-sharing**
> operations: exactly which entities, edges, denormalized fields, audits, and
> permission checks each operation performs — in the TheHive 4 source (graph) and
> the equivalent in our PostgreSQL rewrite (building on the
> [`poc-postgres/`](./poc-postgres/) PoC). Companions:
> [`thehive-data-model.md`](./thehive-data-model.md) (entities/edges),
> [`thehive-api-internals.md`](./thehive-api-internals.md) §6 (the `.visible`/`.can`
> gate), [`thehive4-parity-spec.md`](./thehive4-parity-spec.md) §3.

## The three concepts, precisely

- **Profile** = a named permission set (e.g. `analyst`, `org-admin`). Global.
- **Role / Membership** = the binding **(user, organisation, profile)**. A user
  has *one* role per org and can belong to many orgs.
- **Share** = the binding **(organisation, case, profile, owner?)** that makes a
  case visible to an org with a given permission level, optionally exposing a
  subset of its tasks/observables.

Two different "profiles" act on every case operation, and **both** must hold the
permission (this is the two-level `.can()`):
1. the user's **membership** profile in the current org, and
2. the **share** profile on that case for the current org.

---

# Part 1 — Roles / membership

## 1.1 Add a user to an organisation (create a role)

**Source mechanism** (`RoleSrv.create`, called by `UserSrv.addUserToOrganisation`):
creates a `Role` vertex and **three edges** — there is no direct User→Org edge;
the `Role` vertex is the hub.

```
Role  ──RoleOrganisation──▶ Organisation
User  ──UserRole──────────▶ Role
Role  ──RoleProfile───────▶ Profile
```

- Guarded by "user not already in this org" (`get(user).organisations.getByName`).
- Then `auditSrv.user.create(...)` and an `IntegrityCheck.EntityAdded("User")`
  message (dedup/repair hook).
- `addOrCreateUser` wraps this: create the `User` vertex if new (+ avatar), then
  `addUserToOrganisation`.

**Relational equivalent** — the 3-edge hub collapses to **one row** (PoC V1):

```sql
INSERT INTO membership (user_id, organisation_id, profile_id) VALUES (…)
ON CONFLICT (user_id, organisation_id) DO NOTHING;   -- "already in org" guard
```

## 1.2 Change a user's profile in an org

**Source** (`UserSrv.setProfile`): re-point the `RoleProfile` edge of that user's
role in that org to the new profile; audit `user.changeProfile`.
**Relational:** `UPDATE membership SET profile_id=… WHERE user_id=… AND organisation_id=…`.

## 1.3 Remove a user from an org

**Source:** delete the `Role` vertex (and its 3 edges) for that (user, org); audit
`user.delete` with the org in details. If it was the user's last org, the user
may be locked/removed.
**Relational:** `DELETE FROM membership WHERE user_id=… AND organisation_id=…`.

## 1.4 Profiles, permissions, and org-to-org links

- **Profile**/**permissions**: a profile owns a set of permission strings (PoC:
  `profile` + `profile_permission`). Admin-scope permissions only take effect in
  the admin org (`restrictedPermissions`).
- **Org-to-org link** (`OrganisationOrganisation` edge / `organisation_link`
  table): governs **who may share with whom** — sharing a case to org B requires
  a link from the current org to B. Enforced in the share controller before
  creating the share.

---

# Part 2 — Sharing a case (and its tasks/observables)

Every share operation below is gated by **`.can(Permissions.manageShare)` on the
case** (the two-level check), verified in `ShareCtrl` before calling `ShareSrv`.

## 2.1 The owner share (created with the case)

When a case is created, the owning org gets a `Share` with **`owner = true`**.
This is the share that always keeps the case's tasks/observables visible to the
owner, and it's why unshare-of-a-guest never orphans the owner's data.

## 2.2 Share a case with another organisation

**Source** (`ShareSrv.shareCase(owner=false, case, org, profile)`):

| Step | Effect |
|---|---|
| guard | fail if `(case, org)` share already exists |
| create vertex | `Share(owner=false)` |
| `OrganisationShare` edge | Organisation ▶ Share |
| `ShareCase` edge | Share ▶ Case |
| `ShareProfile` edge | Share ▶ Profile (the guest org's permission level for this case) |
| **denormalize** | add `org._id` to `case.organisationIds` (the indexed visibility field used by `.visible`) |
| audit | `auditSrv.share.shareCase(case, org, profile)` |

API: `POST /case/{caseId}/shares` (body: org + profile). Note: this shares the
**case** only; tasks/observables are shared separately (2.3–2.4) — that's how TH4
supports "share the case but only some tasks".

**Relational equivalent** (extends PoC):

```sql
INSERT INTO case_share (organisation_id, case_id, profile_id, owner)
VALUES (:org, :case, :profile, false);          -- UNIQUE(org,case) = the guard
-- No organisationIds denormalization needed: RLS computes visibility directly
-- from case_share (PoC V3). (Optionally cache org ids on case_ for index speed.)
```

## 2.3 Share **all** tasks / **all** observables of an already-shared case

**Source** (`shareCaseTasks` / `shareCaseObservables`): for every task/observable
of the case **not already shared by this share**, create a `ShareTask` /
`ShareObservable` edge and add `org._id` to the task's/observable's
`organisationIds`.

**Relational:**
```sql
INSERT INTO share_task (share_id, task_id)
SELECT :share, t.id FROM task t
WHERE t.case_id = :case
  AND NOT EXISTS (SELECT 1 FROM share_task st WHERE st.share_id=:share AND st.task_id=t.id);
```
(analogous for `share_observable`).

## 2.4 Share a **single** task / observable

**Source** (`shareTask` / `shareObservable`): look up the case's share for the
org, create one `ShareTask`/`ShareObservable` edge, add the org to the entity's
`organisationIds`, audit the task/observable.
API: `POST /case/task/{taskId}/shares`, `POST /observable/{observableId}/shares`.
**Relational:** `INSERT INTO share_task(share_id, task_id) VALUES (…)`.

## 2.5 Update a share's profile (change a guest org's permission level)

**Source** (`updateProfile`): remove the existing `ShareProfile` edge, create a
new one to the new profile, audit `share.shareCase`.
API: `PATCH /case/share/{shareId}` (body: profile).
**Relational:** `UPDATE case_share SET profile_id=:newProfile WHERE id=:share`.
**Effect:** because the write-permission gate (PoC V5 `can_case`) reads
`case_share.profile`, lowering a guest org to `read-only` **immediately** removes
its ability to mutate the case — no other change needed.

## 2.6 Task `actionRequired` flag

The `ShareTask` edge carries `actionRequired: Boolean` (per share). It marks "this
org needs to act on this task" and is queryable (`/task/{id}/actionRequired`,
`PUT …/actionRequired/{orgId}`).
**Relational:** the `share_task.action_required` column (already in PoC V2).

---

# Part 3 — Unsharing (and orphan cleanup — the subtle part)

## 3.1 Unshare a case

**Source** (`ShareSrv.unshareCase`) — order matters:

1. remove `org._id` from `case.organisationIds`;
2. for the share's **observables**: remove `org._id` from their `organisationIds`,
   then **delete the observable vertex iff it now has zero shares**
   (`…barrier().filterNot(_.shares.range(1,2)).remove()` — keep if ≥1 share
   remains, delete if 0);
3. same for the share's **tasks**;
4. audit `share.unshareCase`;
5. remove the `Share` vertex (its edges go with it).

The "delete iff zero remaining shares" is the **orphan cleanup**: a task/observable
that was only visible through the share being removed (and not the owner share or
any other share) is deleted. Anything still referenced by the owner share (or
another guest) survives.

API: `DELETE /case/{caseId}/shares` (+ `DELETE /case/share/{shareId}`,
`DELETE /case/shares` for bulk).

**Relational equivalent** — the orphan question is *different* and simpler:

```sql
DELETE FROM case_share WHERE id = :share;   -- ON DELETE CASCADE drops its share_task/share_observable rows
```

Because in the relational model **tasks/observables belong to the case
(`case_id` FK), not to a share**, deleting a guest share just removes that org's
*visibility rows* (`share_task`/`share_observable`); the task/observable rows
themselves stay with the case (still owned by the owning org). You only need
explicit orphan deletion if you allow entities that exist **solely** under a
non-owner share — which the owner-share invariant (2.1) prevents. *Decision to
record in M0:* keep the owner-share invariant so "unshare" is a pure
visibility-row delete, never a data delete.

## 3.2 Unshare a single task / observable

**Source** (`unshareTask` / `unshareObservable`): remove `org._id` from the
entity's `organisationIds`, find the `ShareTask`/`ShareObservable` edge belonging
to **that org's share**, remove just that edge, audit.
API: `DELETE /task/{taskId}/shares`, `DELETE /observable/{observableId}/shares`.
**Relational:** `DELETE FROM share_task WHERE task_id=:task AND share_id IN (SELECT id FROM case_share WHERE case_id=:case AND organisation_id=:org)`.

---

# Part 4 — Where the permission check sits on each op

| Operation | Permission gate (both levels) | Extra check |
|---|---|---|
| share case / task / observable | `case.can(manageShare)` | current org **linked** to target org |
| update share profile | `share.case.can(manageShare)` | can't change the owner share's org out |
| unshare (any) | `case.can(manageShare)` | can't remove the **owner** share |
| add user to org / set profile | `manageUser` (org), `manageProfile`/`manageOrganisation` (admin) for cross-org | admin-scope perms only in admin org |

`.can(manageShare)` = **user's membership profile has `manageShare`** AND **the
case's share-to-this-org profile has `manageShare`** (PoC V5 `can_case`). So a
guest org shared in `read-only` can never re-share or unshare the case, even if
its members are analysts in their own org.

---

# Part 5 — What the PoC already proves vs. what these ops add

The [`poc-postgres/`](./poc-postgres/) PoC already proves the **read-visibility**
gate (V3) and the **two-level write-permission** gate (V5). The operations above
are the **write side** that manipulates the gate's inputs:

- `membership` insert/update/delete = §1
- `case_share` insert/update/delete = §2.2, §2.5, §3.1
- `share_task` / `share_observable` insert/delete = §2.3–2.4, §3.2

These are ordinary INSERT/UPDATE/DELETEs **subject to the same RLS** (a user can
only `case_share` for cases they can see, with `manageShare`), so they need no new
enforcement path — they just need their own `manageShare`-gated RESTRICTIVE
policies, exactly like the case-write policies in V5.

> Want this runnable? I can extend the PoC with `share_case()` / `unshare_case()`
> / `share_task()` SQL functions + RESTRICTIVE `manageShare` policies on
> `case_share`/`share_task`, and a demo proving a `read-only` guest org cannot
> re-share or edit — verified like the existing V1–V5.
