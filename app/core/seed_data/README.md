# Seed profiles

Each subdirectory here is a **seed profile** — a set of per-entity JSON files
that the loader (`app/core/seed/loader.py`) applies to the database on local
startup. Which profile is used is controlled by the `SEED_PROFILE` env var
(default `demo`; `none` disables seeding). Only applied when `ENVIRONMENT=local`.

```
seed_data/
  demo/     # rich showcase: 3 narrative cases, dashboard-filler alerts/cases, ...
  dev/      # minimal: one org, one case, a couple of alerts
```

## Files (per profile)

| File | Contains |
| --- | --- |
| `organisation.json` | The org, its users (one marked `"primary": true`), and custom-field definitions. **Required.** |
| `alerts.json` | `{ "alerts": [...] }` — ingested up front; cases promote them by `ref`. |
| `cases.json` | `{ "cases": [...] }` — each with a stable `ref`, plus nested tags, custom-field values, observables, comments. |
| `tasks.json` | `{ "tasks": [...] }` — each references a case by `ref`; work logs nest under `logs`. |
| `knowledge_base.json` | `{ "pages": [...] }` |
| `sla.json` | `{ "policies": [...] }` |
| `dashboards.json` | `{ "dashboards": [...] }` — `owner` is `"primary"` or `"superadmin"`. |

Every file except `organisation.json` is optional. Content is validated against
the Pydantic models in `app/core/seed/schema.py` on load, so a malformed profile
fails fast at startup.

## Relative times

Timestamps are **offset strings** relative to a single `now` captured at seed
time, so a freshly seeded DB always looks "live":

- `"-4h"` — four hours ago · `"-3d"` — three days ago · `"+2h30m"` — in 2½ hours
- Units `d` / `h` / `m`, any subset, signed. See `parse_offset` in `schema.py`.

## Linking

- **Alert → case**: set `"link_alert": "<alert ref>"` on a case; the alert is
  promoted into it (status → Imported).
- **Task → case**: set `"case": "<case ref>"` on a task.
- **Enums** use member names: task `status` is `waiting` / `in_progress` /
  `completed`; case `resolution` is `true_positive` / `false_positive` /
  `indeterminate` / `duplicated` / etc.

## Idempotency

Loading is idempotent — orgs/users/cases/tasks/logs/dashboards/pages are guarded
by identity (id / email / title / name / message), so re-running back-fills new
entries without duplicating existing ones.

## Regenerating

Author JSON by hand, or edit and re-run the loader. To bulk-produce a profile
from code, build `SeedProfile` objects and dump with
`model.model_dump(mode="json", exclude_defaults=True, exclude_none=True)`.
