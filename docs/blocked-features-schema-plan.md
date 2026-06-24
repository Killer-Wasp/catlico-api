# Blocked-feature backends — schema layer (models + migrations)

Schema-layer plan for the five web features blocked on missing tables: **SLA
Policies, API Keys, Notification Rules/Notifiers, Knowledge Base, Functions**.
This pass delivers SQLModel tables + I/O schemas + Alembic migrations only — the
CRUD/route wire-up is Workstream A in
[backend-gap-roadmap.md](backend-gap-roadmap.md).

> **Status (verified)**: All items DONE. The 5 hand-written Alembic migrations created
> and verified — `alembic upgrade head` applies cleanly, `alembic downgrade -5` + re-upgrade
> round-trips, `import app.models` passes, `uv run pytest` 227 green.
> Head: `e6b8d1f3a5c7` (`add functions`).

Reference template: the `custom_field` trio
([app/models/custom_field.py](../app/models/custom_field.py) +
[its migration](../alembic/versions/c7f3a9b2e4d1_add_custom_fields.py)).
Conventions: `TimestampMixin`/`SoftDeleteMixin`/`CreatedMixin` from
[common.py](../app/models/common.py); org-scoping via `organisation_id` FK
(`ondelete="CASCADE"`, indexed); JSON via
`Field(default_factory=..., sa_column=Column(JSON))`; `str`-Enums; `Create`/`Update`/`Public`
schemas co-located; every model imported in
[__init__.py](../app/models/__init__.py) so Alembic metadata sees it.

Field shapes are derived from the existing frontend so tables match what the UI
already renders: `catlico-web` `src/components/Settings/settingsData.ts`
(SLA/API keys/notifiers/rules), `src/components/KnowledgeBase/knowledgeBase.types.ts`,
and the `FunctionAutomation` type in `src/components/pages/FunctionsPage.tsx`.

---

## DONE — models, schemas, permissions, registration

All five model files created, import cleanly, and register their tables
(`uv run python -c "import app.models"` confirms all 7 tables; KB
discriminated-union parses; builtin-role permission sets correct):

- [app/models/sla.py](../app/models/sla.py) — `sla_policy` (+ `SlaPolicyUpsert/Public`); unique `(organisation_id, severity)`; durations as seconds.
- [app/models/api_key.py](../app/models/api_key.py) — `api_key` (TimestampMixin + SoftDelete; `key_hash` = sha256 of token, `prefix`+`last_four` for the masked `thp_**********3f9a` display, `scopes` JSON; plaintext never stored) (+ `ApiKeyCreate/Public/Created`, the last carrying the one-time `key`).
- [app/models/notification.py](../app/models/notification.py) — `notifier` (enum `NotifierType{slack,email,webhook,kafka}`, `target`, `config` JSON, `secrets_encrypted`) + `notification_rule` (`event` trigger key, `notifier_ids` JSON) (+ Create/Update/Public for each).
- [app/models/knowledge_base.py](../app/models/knowledge_base.py) — `knowledge_base_page` (TimestampMixin + SoftDelete; `tags`/`blocks` JSON, `blocks` validated by the `KnowledgeBaseBlock` paragraph|section|list Pydantic discriminated union; `author`→`created_by`, `updated`→`updated_at`) (+ Create/Update/Public).
- [app/models/function.py](../app/models/function.py) — `function` (enums `FunctionRuntime`, `FunctionTrigger`; `trigger_config`/`secrets` JSON; `timeout_ms`, `egress`, `approval`; denormalised `run_count`/`error_count`) + `function_run` (CreatedMixin, immutable, enum `FunctionRunStatus`) (+ Create/Update/Public, Public nests `FunctionRunPublic`).

Plus:
- [app/models/role.py](../app/models/role.py) — added `read/write:knowledge_base` and `read/write/run:function` to the `Permission` enum, `ORG_PERMISSIONS`, and `BUILTIN_ROLES` (org-admin: all 5; analyst: read/write KB + read/run function; read-only: read KB + read function). SLA / API keys / notifications get **no** new permission — org-admin-gated at the route layer later.
- [app/models/__init__.py](../app/models/__init__.py) — imported `ApiKey`, `Function`+`FunctionRun`, `KnowledgeBasePage`, `NotificationRule`+`Notifier`, `SlaPolicy`.

---

## REMAINING — the 5 Alembic migrations (hand-write, do not autogenerate)

Current head: **`d4e5f6a7b8c9`** (`add_case_to_attachmentownertype`). Add five
hand-written migrations under [alembic/versions/](../alembic/versions/), chained in
order, each mirroring the `custom_fields` migration (`op.create_table` with explicit
columns/indexes; `downgrade()` drops tables and, on Postgres, the created `Enum`
types via `sa.Enum(name=...).drop(bind, checkfirst=True)`). String cols →
`sqlmodel.sql.sqltypes.AutoString()`; UUID → `sa.Uuid()`; timestamps →
`sa.DateTime(timezone=True)`; JSON → `sa.JSON()`.

> Revision IDs below are **placeholder suggestions**. Real revision IDs are
> assigned by `alembic revision` at creation time. Use `--rev-id=<id>` to match
> these or let Alembic assign its own.

| # | revises → revision | file (suggested) | tables |
|---|---|---|---|
| 1 | `d4e5f6a7b8c9` → `a1d3f5b7c9e2` | `..._add_sla_policies.py` | `sla_policy` |
| 2 | `a1d3f5b7c9e2` → `b2e4f6a8c1d3` | `..._add_api_keys.py` | `api_key` |
| 3 | `b2e4f6a8c1d3` → `c3f5a7b9d2e4` | `..._add_notifications.py` | `notifier`, `notification_rule` |
| 4 | `c3f5a7b9d2e4` → `d5a7c9e2f4b6` | `..._add_knowledge_base.py` | `knowledge_base_page` |
| 5 | `d5a7c9e2f4b6` → `e6b8d1f3a5c7` | `..._add_functions.py` | `function`, `function_run` |

Per-migration notes:
- **Indexes**: one `ix_<table>_organisation_id` per org-scoped table; plus `ix_api_key_key_hash`, `ix_knowledge_base_page_title`, `ix_function_name`; plus `ix_<table>_deleted_at` for the soft-delete tables (api_key, knowledge_base_page, function); `ix_function_run_function_id`.
- **Unique**: `uq_sla_policy_org_sev` on `(organisation_id, severity)`.
- **Enum types created**: `notifiertype` (mig 3); `functionruntime` + `functiontrigger` + `functionrunstatus` (mig 5). Drop them in the matching `downgrade()`.
- **Permission backfill** (copy the INSERT/DELETE block from `c7f3a9b2e4d1`): mig 4 inserts `read:knowledge_base` for org-admin/analyst/read-only and `write:knowledge_base` for org-admin/analyst; mig 5 inserts `read:function` for all three, `run:function` for org-admin/analyst, `write:function` for org-admin. Downgrades delete those permission strings. (No-op on a fresh DB where roles seed after migrate.)

---

## Verification

- **Imports/metadata**: `uv run python -c "import app.models"` — passes.
- **Migration round-trip** (needs Docker Postgres): `make db`, then `uv run alembic upgrade head` (all 5 apply), `uv run alembic downgrade -5` and back up (reversible), `uv run alembic check` (no model/migration drift).
- **Tests**: `uv run pytest` (testcontainers) stays green — exercises `init_db` seeding + `BUILTIN_ROLES`, so new permissions/tables must not break startup.
- **Shape parity**: each `*Public` lines up with the frontend type (`FunctionAutomation`, `KnowledgeBasePage`, the `settingsData.ts` tuples) so the later CRUD/route pass is a straight wire-up.

## After migrations — what unlocks

With all 5 migrations applied, the DB has tables for every Workstream A feature.
The follow-up pass (Workstream A in the roadmap, phase 2–4) builds on top:

| Phase | Feature | What's built on these tables |
|---|---|---|
| 2 | API keys, SLA, Knowledge Base | CRUD + route + mount (models → REST) |
| 3 | Notifiers, Notification rules, User feed | CRUD + outbox consumers + notifier dispatch |
| 4 | Functions | CRUD + toggle + sandboxed test-run (reuses konnect isolation) |

## Out of scope (explicit)
No `app/crud/*`, no `app/api/v1/routes/*`, no `v1/main.py` mounting, no token
generation/hashing helpers, no outbox/notifier dispatch — the follow-up
"wire the frontend" pass (Workstream A in the roadmap).
