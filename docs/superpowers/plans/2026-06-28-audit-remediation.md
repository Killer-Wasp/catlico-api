# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the changed guidance docs back into agreement with live API/Konnect code and remove the stray unused import introduced in the notification CRUD module.

**Architecture:** This is a documentation-accuracy and cleanup pass, not a product-feature pass. Treat live code, tests, and existing migrations as source of truth; update guidance docs to describe current partial implementations precisely so future implementation agents do not duplicate existing work or skip unfinished seams.

**Tech Stack:** Markdown docs, Python 3.14, FastAPI/SQLModel API repo, Catlico Konnect Python worker, pytest.

---

## File Structure

- Modify `/Users/local/catlico/catlico-api/AGENTS.md`: correct stale backend current-state bullets for MITRE Pattern/Procedure, function run/toggle API, and partial responder plumbing.
- Modify `/Users/local/catlico/catlico-api/app/crud/notification.py`: remove unused `NotifierDelivery` import.
- Modify `/Users/local/catlico/catlico-konnect/AGENTS.md`: correct stale Konnect current-state bullets for partial responder SDK/client/worker scaffolding.
- Modify `/Users/local/catlico/catlico-konnect/docs/gap-analysis.md`: keep the detailed gap analysis aligned with live responder scaffolding.
- Modify `/Users/local/catlico/catlico-konnect/connectors/RESPONDERS.md`: change “first responder implemented” language to “first responder candidate” language because `connectors/Test/` does not exist yet.

Do not change runtime behavior in this remediation. Do not add new responder packages, function runtime code, migrations, or API routes.

---

### Task 1: Correct Backend AGENTS Current State

**Files:**
- Modify: `/Users/local/catlico/catlico-api/AGENTS.md`

- [ ] **Step 1: Verify live backend facts before editing**

Run:

```bash
cd /Users/local/catlico/catlico-api
rg -n "class Pattern|class Procedure|pattern_router|/run|/toggle|ConnectorType|responder|connector_operations" app
```

Expected:

- `app/models/pattern.py` defines `Pattern` and `Procedure`.
- `app/api/v1/routes/patterns.py` defines pattern/procedure routes.
- `app/api/v1/routes/functions.py` defines `/run`, `/runs`, and `/toggle`.
- `app/models/connector.py` has `ConnectorType.responder`.
- `app/api/internal/routes/responder.py` and `app/services/connector_operations.py` exist.

- [ ] **Step 2: Replace stale backend deferred bullets**

In `/Users/local/catlico/catlico-api/AGENTS.md`, replace the `Built:` bullet group under `## Current state` with:

```markdown
Built:

- Auth: JWT login, persisted refresh tokens, and `/auth/refresh` that re-reads membership.
- Users, organisations, roles/RBAC, org members, and organisation links/auto-share.
- Cases, case shares, case merge, tasks, task queue, logs, observables, observable types, alerts, comments, flags, tags, case templates, attachments, custom fields, and MITRE Pattern/Procedure linkage.
- Alert promotion and alert-to-case merge paths.
- Connectors plus manual observable enrichment: public enqueue/read endpoints and internal analyzer worker register/claim/result endpoints.
- Partial responder plumbing: `ConnectorType.responder`, internal responder routes, and `connector_operations` operation application service exist, but responder jobs/connectors are not yet fully productized.
- Web-facing admin/settings backends: API keys, SLA policies, knowledge base pages, functions CRUD/run records, notifiers, and notification rules.
- Audit and outbox write path, case activity feed, and global superadmin audit search.
```

- [ ] **Step 3: Replace stale backend partial/deferred bullets**

In the same file, replace the `Still deferred or partial:` bullet group with:

```markdown
Still deferred or partial:

- Real outbox consumers for stream/notification/connector fan-out are not registered yet.
- Notification rules and notifiers have CRUD and delivery data models, but no complete production delivery/test-send path.
- Functions have CRUD, run records, manual run/list-runs/toggle APIs, and permission checks, but no sandboxed execution worker or scoped function-token runtime yet.
- API keys have CRUD and hashing, but request authentication still uses JWT only.
- Analyzer/responder `operations` plumbing is partial: operation schemas and an application service exist, but responder job queueing, real responder connectors, confirmation UX, and full integration tests are not complete.
```

- [ ] **Step 4: Verify no stale backend phrases remain**

Run:

```bash
cd /Users/local/catlico/catlico-api
rg -n "Pattern / Procedure.*absent|toggle/test-run|reserved for a later milestone|current connector execution is analyzer-only|no sandboxed execution/toggle/test-run pipeline" AGENTS.md
```

Expected:

- No matches.

- [ ] **Step 5: Verify positive backend wording exists**

Run:

```bash
cd /Users/local/catlico/catlico-api
rg -n "MITRE Pattern/Procedure linkage|Partial responder plumbing|manual run/list-runs/toggle APIs|sandboxed execution worker|operations plumbing is partial" AGENTS.md
```

Expected:

- Matches for each corrected current-state concept.

- [ ] **Step 6: Commit backend AGENTS correction**

```bash
cd /Users/local/catlico/catlico-api
git add AGENTS.md
git commit -m "docs: correct backend agent current state"
```

---

### Task 2: Remove Notification CRUD Unused Import

**Files:**
- Modify: `/Users/local/catlico/catlico-api/app/crud/notification.py`
- Test: `/Users/local/catlico/catlico-api/tests/test_notifier_delivery.py`

- [ ] **Step 1: Remove the unused import**

In `/Users/local/catlico/catlico-api/app/crud/notification.py`, change this import block:

```python
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierDelivery,
    NotifierUpdate,
    NotificationRule,
    NotificationRuleCreate,
    NotificationRuleUpdate,
    UserNotification,
    UserNotificationUpdate,
)
```

to:

```python
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierUpdate,
    NotificationRule,
    NotificationRuleCreate,
    NotificationRuleUpdate,
    UserNotification,
    UserNotificationUpdate,
)
```

- [ ] **Step 2: Verify the import is gone**

Run:

```bash
cd /Users/local/catlico/catlico-api
rg -n "NotifierDelivery" app/crud/notification.py
```

Expected:

- No matches.

- [ ] **Step 3: Run targeted notification tests**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_notifier_delivery.py
```

Expected:

- All tests pass.

- [ ] **Step 4: Commit notification cleanup**

```bash
cd /Users/local/catlico/catlico-api
git add app/crud/notification.py
git commit -m "chore: remove unused notification import"
```

---

### Task 3: Correct Konnect AGENTS Current State

**Files:**
- Modify: `/Users/local/catlico/catlico-konnect/AGENTS.md`

- [ ] **Step 1: Verify live Konnect responder facts before editing**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "class Responder|class ActionResult|responder_register|responder_claim_work|responder_submit_result|_execute_responder|connector_type == \"responder\"" sdk konnect
```

Expected:

- `sdk/konnect_sdk/responder.py` defines `Responder`, `Operation`, and `ActionResult`.
- `konnect/client.py` has responder endpoint methods.
- `konnect/worker.py` has a responder execution branch.

- [ ] **Step 2: Replace stale analyzer-only overview sentence**

In `/Users/local/catlico/catlico-konnect/AGENTS.md`, replace:

```markdown
The worker is analyzer-only today. Responder jobs, file/blob inputs, local-tool/daemon connectors, and function execution are separate milestones.
```

with:

```markdown
The production polling path is still analyzer-first: the main loop registers and claims analyzer work from `/api/internal/analyzer`. Partial responder scaffolding exists (`Responder`, `ActionResult`, responder client methods, and a worker execution branch), but real responder job polling, installed responder connector packages, and end-to-end responder tests remain separate milestones. File/blob inputs, local-tool/daemon connectors, and function execution are also separate milestones.
```

- [ ] **Step 3: Add responder scaffolding to Built**

In the `Built:` list, add this bullet after the worker client bullet:

```markdown
- Partial responder SDK/client/worker scaffolding: `Responder`, `Operation`, `ActionResult`, responder endpoint methods, and a responder execution branch exist.
```

- [ ] **Step 4: Replace stale responder deferred bullet**

Replace:

```markdown
- **Responder support**: no `Responder` base class, operation result model, responder client endpoints, or worker branch.
```

with:

```markdown
- **Responder support**: scaffolding exists, but the main poll loop still claims analyzer work only, no responder packages are installed under `connectors/`, and responder registration/claim/submit is not yet proven by end-to-end tests.
```

- [ ] **Step 5: Adjust docs pointer**

Replace:

```markdown
- `docs/gap-analysis.md` is the best current map of missing responder, blob, local-tool, and porting work.
```

with:

```markdown
- `docs/gap-analysis.md` is the current map of incomplete responder, blob, local-tool, and porting work.
```

- [ ] **Step 6: Verify no stale Konnect AGENTS phrases remain**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "worker is analyzer-only today|no `Responder` base class|no responder claim/submit endpoints|no worker branch" AGENTS.md
```

Expected:

- No matches.

- [ ] **Step 7: Verify positive Konnect wording exists**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "Partial responder scaffolding|Partial responder SDK/client/worker scaffolding|main poll loop still claims analyzer work only" AGENTS.md
```

Expected:

- Matches for each corrected current-state concept.

- [ ] **Step 8: Commit Konnect AGENTS correction**

```bash
cd /Users/local/catlico/catlico-konnect
git add AGENTS.md
git commit -m "docs: correct konnect agent current state"
```

---

### Task 4: Correct Konnect Gap And Responder Catalog Docs

**Files:**
- Modify: `/Users/local/catlico/catlico-konnect/docs/gap-analysis.md`
- Modify: `/Users/local/catlico/catlico-konnect/connectors/RESPONDERS.md`

- [ ] **Step 1: Update core worker summary in gap analysis**

In `/Users/local/catlico/catlico-konnect/docs/gap-analysis.md`, replace:

```markdown
- `konnect/client.py` — `register()` / `claim_work()` / `submit_result()` (Bearer auth, 5xx retry, 409 conflict handling). Hardwired to `/api/internal/analyzer` prefix — no responder path.
- `konnect/registry.py` — entry-point discovery + allowlist filtering + version extraction.
- `sdk/konnect_sdk/connector.py`, `extractor.py` — `Connector` ABC (single base, `connector_type` hardcoded `"analyzer"`), `WorkInput`/`Report`/`Taxonomy`/`Artifact` models, `EmptyConfig`, `ConnectorError`/`TlpError`, heuristic IOC auto-extract, manifest generation.
```

with:

```markdown
- `konnect/client.py` — analyzer `register()` / `claim_work()` / `submit_result()` plus responder endpoint methods; bearer auth, 5xx retry, and 409 conflict handling are implemented.
- `konnect/registry.py` — entry-point discovery + allowlist filtering + version extraction.
- `sdk/konnect_sdk/connector.py`, `extractor.py`, `responder.py` — `Connector` ABC, `Responder` ABC, `WorkInput`/`Report`/`Taxonomy`/`Artifact`/`Operation`/`ActionResult` models, `EmptyConfig`, `ConnectorError`/`TlpError`, heuristic IOC auto-extract, manifest generation.
```

- [ ] **Step 2: Replace stale Gap 1 heading and body**

Replace the `## Gap 1 — Responder support (NOT STARTED) — highest impact` section body up to the next `---` with:

```markdown
## Gap 1 — Responder support (PARTIAL SCAFFOLDING) — highest impact

Responder scaffolding exists, but end-to-end responder support is not complete.

Built:

- SDK `Responder`, `Operation`, and `ActionResult` models in `sdk/konnect_sdk/responder.py`.
- Responder client methods in `konnect/client.py`.
- Worker `_execute_responder()` and `connector_type == "responder"` branch in `konnect/worker.py`.
- Source catalog docs in `docs/responders-porting-matrix.md` and `connectors/RESPONDERS.md`.

Still missing:

- Main worker loop does not poll `/api/internal/responder/work`; it still calls analyzer `claim_work()`.
- No installed responder connector package exists under `connectors/` yet.
- No `connectors/Test/` package exists yet.
- No end-to-end test proves responder registration, work claim, operation submission, and backend case mutation together.
- API responder endpoints are still stub/partial and need the backend responder job model/queue to mature.

Needs (cross-repo): real responder job queueing; API responder lease/claim/result shape; worker polling of responder jobs; first `Test` responder package; one low-risk notification responder; confirmation/dry-run policy for high-risk responders; integration tests that prove operations apply through audited backend service paths.

In-tree indicator: `connectors/AILOnionLookup` has a comment noting the original emitted responder-style `operations()` (AddTagToArtifact); the local analyzer port still does not submit operations through a completed responder channel.

---
```

- [ ] **Step 3: Correct RESPONDERS.md status**

In `/Users/local/catlico/catlico-konnect/connectors/RESPONDERS.md`, replace:

```markdown
Status: E0 catalog complete — 48 upstream Cortex responders classified. See [responders-porting-matrix.md](../docs/responders-porting-matrix.md) for the full matrix.
```

with:

```markdown
Status: E0 catalog complete — 48 upstream Cortex responders classified. No responder connector packages are implemented yet. See [responders-porting-matrix.md](../docs/responders-porting-matrix.md) for the full matrix.
```

- [ ] **Step 4: Correct Test responder section**

Replace:

```markdown
## First responder: Test

The `Test` responder proves the full Catlico Konnect responder lifecycle:
- SDK `Responder` base class → registration → work claim → operation submission → case mutation.

Implementation path: `connectors/Test/`
```

with:

```markdown
## First responder candidate: Test

The `Test` responder should be the first implementation because it can prove the full Catlico Konnect responder lifecycle without external side effects:

- SDK `Responder` base class
- registration
- work claim
- operation submission
- backend case mutation through audited service paths

Planned implementation path: `connectors/Test/`
```

- [ ] **Step 5: Verify responder docs no longer claim built packages**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "NOT STARTED|no `Responder` base class|no responder claim/submit endpoints|Implementation path: `connectors/Test/`|proves the full Catlico Konnect responder lifecycle" docs/gap-analysis.md connectors/RESPONDERS.md
```

Expected:

- No matches.

- [ ] **Step 6: Verify positive responder-doc wording exists**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "PARTIAL SCAFFOLDING|No responder connector packages are implemented yet|First responder candidate|Planned implementation path: `connectors/Test/`" docs/gap-analysis.md connectors/RESPONDERS.md
```

Expected:

- Matches for each corrected concept.

- [ ] **Step 7: Commit Konnect responder docs correction**

```bash
cd /Users/local/catlico/catlico-konnect
git add docs/gap-analysis.md connectors/RESPONDERS.md
git commit -m "docs: clarify partial responder scaffolding"
```

---

### Task 5: Final Verification

**Files:**
- Verify: `/Users/local/catlico/catlico-api/AGENTS.md`
- Verify: `/Users/local/catlico/catlico-api/app/crud/notification.py`
- Verify: `/Users/local/catlico/catlico-konnect/AGENTS.md`
- Verify: `/Users/local/catlico/catlico-konnect/docs/gap-analysis.md`
- Verify: `/Users/local/catlico/catlico-konnect/connectors/RESPONDERS.md`

- [ ] **Step 1: Run API targeted tests**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_notifier_delivery.py
```

Expected:

- All tests pass.

- [ ] **Step 2: Run API stale-doc scan**

Run:

```bash
cd /Users/local/catlico/catlico-api
rg -n "Pattern / Procedure.*absent|toggle/test-run|reserved for a later milestone|current connector execution is analyzer-only|no sandboxed execution/toggle/test-run pipeline|NotifierDelivery" AGENTS.md app/crud/notification.py
```

Expected:

- No matches.

- [ ] **Step 3: Run Konnect stale-doc scan**

Run:

```bash
cd /Users/local/catlico/catlico-konnect
rg -n "worker is analyzer-only today|no `Responder` base class|no responder claim/submit endpoints|no worker branch|NOT STARTED|Implementation path: `connectors/Test/`" AGENTS.md docs/gap-analysis.md connectors/RESPONDERS.md
```

Expected:

- No matches.

- [ ] **Step 4: Inspect final diffs**

Run:

```bash
cd /Users/local/catlico/catlico-api
git diff --check HEAD
git diff --stat HEAD

cd /Users/local/catlico/catlico-konnect
git diff --check HEAD
git diff --stat HEAD
```

Expected:

- `git diff --check HEAD` reports no whitespace errors in both repos.
- API diff is limited to `AGENTS.md` and `app/crud/notification.py`.
- Konnect diff is limited to `AGENTS.md`, `docs/gap-analysis.md`, and `connectors/RESPONDERS.md`.

- [ ] **Step 5: Final commit if prior tasks were batched**

If tasks were not committed individually, commit now:

```bash
cd /Users/local/catlico/catlico-api
git add AGENTS.md app/crud/notification.py
git commit -m "docs: align backend audit guidance"

cd /Users/local/catlico/catlico-konnect
git add AGENTS.md docs/gap-analysis.md connectors/RESPONDERS.md
git commit -m "docs: align konnect responder guidance"
```

---

## Self-Review

- Spec coverage: Covers all audit findings: stale backend MITRE/function/responder docs, stale Konnect responder docs, and unused `NotifierDelivery` import. Also covers adjacent responder catalog mismatch discovered during planning.
- Placeholder scan: Checked for red-flag placeholder wording from the plan-writing guidance; none remains in the executable steps.
- Type consistency: Uses existing names confirmed in live code: `Pattern`, `Procedure`, `FunctionRun`, `ConnectorType.responder`, `Responder`, `Operation`, `ActionResult`, `NotifierDelivery`.
