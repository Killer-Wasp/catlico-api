# Knowledge Base Version History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add permission-preserving knowledge base edit history, contributors, revert, JSON export, and JSON import across the API and web app.

**Architecture:** Keep `knowledge_base_page` as the current-state read model and add append-only `knowledge_base_page_version` rows for snapshots. API responses enrich pages with contributor metadata derived from version history. The web app keeps the current KB detail layout and adds contributor display plus history, revert, import, and export actions through focused query helpers.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async sessions, Alembic, Pytest, TanStack Query, TanStack Router, Mantine, Vitest, Testing Library.

---

## File Structure

API repo: `/Users/local/catlico/catlico-api`

- Create `alembic/versions/n0d3f5a7b9c1_kb_version_history.py`: creates `knowledge_base_page_version`.
- Modify `app/models/knowledge_base.py`: adds version/action/public/import/export/contributor schemas.
- Modify `app/models/__init__.py`: imports the new table model.
- Modify `app/crud/knowledge_base.py`: owns snapshot creation, contributor summaries, version list, revert, import, and export assembly.
- Modify `app/api/v1/routes/knowledge_bases.py`: exposes version, revert, import, and export endpoints.
- Modify `tests/test_api_blocked_features.py`: API regression tests for permissions, contributors, versions, revert, import, and export.
- Modify `tests/test_models_blocked_features.py`: model insert test for the version table.

Web repo: `/Users/local/catlico/catlico-web`

- Modify `src/components/KnowledgeBase/knowledgeBaseQueries.ts`: adds types and query/mutation functions for versions, revert, import, and export.
- Modify `src/components/pages/knowledge-base/model.ts`: maps contributors and last editor into `KBPage`.
- Modify `src/components/pages/KnowledgeBasePage.tsx`: renders contributors, opens history drawer, reverts, imports, and exports.
- Modify `tests/components/pages/KnowledgeBasePage.test.tsx`: covers contributors, history, revert, import, and export behavior.

The current branch already has in-flight KB changes for numeric ids and `content`; do not revert them. Ignore unrelated dirty files in `catlico-web/tests/components/Cases/caseTemplatesQueries.test.ts` and `catlico-web/tests/routes/_app/case-templates/-caseTemplateEditor.test.tsx`.

---

### Task 1: API Version Model And Migration

**Files:**
- Create: `/Users/local/catlico/catlico-api/alembic/versions/n0d3f5a7b9c1_kb_version_history.py`
- Modify: `/Users/local/catlico/catlico-api/app/models/knowledge_base.py`
- Modify: `/Users/local/catlico/catlico-api/app/models/__init__.py`
- Test: `/Users/local/catlico/catlico-api/tests/test_models_blocked_features.py`

- [ ] **Step 1: Write the failing model test**

Add this test near the existing knowledge base model tests in `tests/test_models_blocked_features.py`:

```python
async def test_knowledge_base_page_version_insert(session, org_a, analyst_a):
    from sqlmodel import select

    from app.models.knowledge_base import (
        KnowledgeBasePage,
        KnowledgeBasePageVersion,
    )

    page = KnowledgeBasePage(
        organisation_id=org_a.id,
        title="Runbook",
        summary="A phishing runbook",
        tags=["phishing"],
        content="Initial content",
        created_by=str(analyst_a.id),
    )
    session.add(page)
    await session.flush()

    version = KnowledgeBasePageVersion(
        page_id=page.id,
        organisation_id=org_a.id,
        version_number=1,
        action="create",
        snapshot={
            "title": "Runbook",
            "summary": "A phishing runbook",
            "tags": ["phishing"],
            "content": "Initial content",
        },
        changed_fields=["title", "summary", "tags", "content"],
        edited_by=str(analyst_a.id),
        edited_by_email=analyst_a.email,
    )
    session.add(version)
    await session.flush()

    result = await session.execute(select(KnowledgeBasePageVersion))
    saved = result.scalar_one()
    assert saved.page_id == page.id
    assert saved.version_number == 1
    assert saved.action == "create"
    assert saved.snapshot["content"] == "Initial content"
    assert saved.changed_fields == ["title", "summary", "tags", "content"]
    assert saved.edited_by_email == analyst_a.email
```

- [ ] **Step 2: Run the model test to verify it fails**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_models_blocked_features.py::test_knowledge_base_page_version_insert -q
```

Expected: FAIL because `KnowledgeBasePageVersion` does not exist or the table is missing.

- [ ] **Step 3: Add the model and public schemas**

In `app/models/knowledge_base.py`, change the datetime import and add the typing import:

```python
from datetime import UTC, datetime
from typing import Literal
```

Then add these models after `KnowledgeBasePage`:

```python
KnowledgeBaseVersionAction = Literal["create", "update", "revert", "import"]


class KnowledgeBaseContributor(SQLModel):
    id: str
    email: str
    last_edited_at: datetime


class KnowledgeBasePageVersion(TimestampMixin, table=True):
    __tablename__ = "knowledge_base_page_version"

    id: int | None = Field(default=None, primary_key=True)
    page_id: int = Field(
        foreign_key="knowledge_base_page.id", index=True, ondelete="CASCADE"
    )
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    version_number: int = Field(index=True)
    action: str = Field(index=True)
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    changed_fields: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    edited_by: str
    edited_by_email: str
    edited_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    reverted_from_version_id: int | None = Field(
        default=None, foreign_key="knowledge_base_page_version.id"
    )
    created_by: str = Field(default="system")
```

Also update `KnowledgeBasePagePublic`:

```python
class KnowledgeBasePagePublic(SQLModel):
    id: int
    title: str
    summary: str
    tags: list[str]
    content: str
    organisation_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime | None
    contributors: list[KnowledgeBaseContributor] = []
    last_edited_by: KnowledgeBaseContributor | None = None
```

Add version/import/export schemas:

```python
class KnowledgeBasePageVersionPublic(SQLModel):
    id: int
    page_id: int
    version_number: int
    action: str
    snapshot: dict
    changed_fields: list[str]
    edited_by: str
    edited_by_email: str
    edited_at: datetime
    reverted_from_version_id: int | None = None


class KnowledgeBasePageExport(SQLModel):
    page: KnowledgeBasePagePublic
    versions: list[KnowledgeBasePageVersionPublic]


class KnowledgeBasePageImport(SQLModel):
    page: KnowledgeBasePageCreate
    versions: list[KnowledgeBasePageVersionPublic] = []
```

In `app/models/__init__.py`, change the KB import to include both table models:

```python
from app.models.knowledge_base import KnowledgeBasePage, KnowledgeBasePageVersion  # noqa: F401
```

- [ ] **Step 4: Add the Alembic migration**

Create `alembic/versions/n0d3f5a7b9c1_kb_version_history.py`:

```python
"""kb version history

Revision ID: n0d3f5a7b9c1
Revises: m9c2d4e6f8a0
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n0d3f5a7b9c1"
down_revision: str | None = "m9c2d4e6f8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base_page_version",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
        sa.Column("edited_by", sa.String(), nullable=False),
        sa.Column("edited_by_email", sa.String(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("reverted_from_version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["knowledge_base_page.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reverted_from_version_id"], ["knowledge_base_page_version.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_base_page_version_page_id",
        "knowledge_base_page_version",
        ["page_id"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_organisation_id",
        "knowledge_base_page_version",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_version_number",
        "knowledge_base_page_version",
        ["version_number"],
    )
    op.create_index(
        "ix_knowledge_base_page_version_action",
        "knowledge_base_page_version",
        ["action"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_base_page_version_action", table_name="knowledge_base_page_version")
    op.drop_index("ix_knowledge_base_page_version_version_number", table_name="knowledge_base_page_version")
    op.drop_index("ix_knowledge_base_page_version_organisation_id", table_name="knowledge_base_page_version")
    op.drop_index("ix_knowledge_base_page_version_page_id", table_name="knowledge_base_page_version")
    op.drop_table("knowledge_base_page_version")
```

- [ ] **Step 5: Run the model test to verify it passes**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_models_blocked_features.py::test_knowledge_base_page_version_insert -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-api
git add app/models/knowledge_base.py app/models/__init__.py tests/test_models_blocked_features.py alembic/versions/n0d3f5a7b9c1_kb_version_history.py
git commit -m "feat: add kb version history model"
```

---

### Task 2: API Snapshot CRUD And Contributors

**Files:**
- Modify: `/Users/local/catlico/catlico-api/app/crud/knowledge_base.py`
- Modify: `/Users/local/catlico/catlico-api/app/api/v1/routes/knowledge_bases.py`
- Test: `/Users/local/catlico/catlico-api/tests/test_api_blocked_features.py`

- [ ] **Step 1: Write failing API tests for create, update, contributors, and read-only write denial**

Add these tests after `test_kb_crud` in `tests/test_api_blocked_features.py`:

```python
async def test_kb_create_and_update_record_versions_and_contributors(
    client: AsyncClient, org_a, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={
            "title": "Initial",
            "summary": "First summary",
            "tags": ["one"],
            "content": "First content",
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    page_id = created.json()["id"]
    assert created.json()["contributors"][0]["email"] == analyst_a.email
    assert created.json()["last_edited_by"]["email"] == analyst_a.email

    updated = await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"summary": "Second summary", "content": "Second content"},
        headers=h,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["contributors"][0]["email"] == analyst_a.email
    assert updated.json()["last_edited_by"]["email"] == analyst_a.email

    history = await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)
    assert history.status_code == 200, history.text
    versions = history.json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["action"] == "update"
    assert versions[0]["changed_fields"] == ["summary", "content"]
    assert versions[0]["snapshot"]["content"] == "Second content"
    assert versions[1]["action"] == "create"


async def test_kb_readonly_user_cannot_mutate_history_actions(
    client: AsyncClient,
    org_a,
    analyst_a_token,
    readonly_a_token,
):
    writer_h = _h(analyst_a_token, org_a.id)
    reader_h = _h(readonly_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Protected", "content": "Only writers edit"},
        headers=writer_h,
    )
    page_id = created.json()["id"]

    patch = await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"content": "reader edit"},
        headers=reader_h,
    )
    assert patch.status_code == 403

    delete = await client.delete(f"/api/v1/knowledge-base/{page_id}", headers=reader_h)
    assert delete.status_code == 403

    history = await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=reader_h)
    assert history.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_create_and_update_record_versions_and_contributors tests/test_api_blocked_features.py::test_kb_readonly_user_cannot_mutate_history_actions -q
```

Expected: FAIL because version endpoints and contributor fields are not implemented.

- [ ] **Step 3: Implement snapshot helpers and contributor enrichment**

In `app/crud/knowledge_base.py`, import the new models and SQL helpers:

```python
from collections.abc import Iterable

from sqlmodel import desc, func

from app.models.knowledge_base import (
    KnowledgeBaseContributor,
    KnowledgeBasePage,
    KnowledgeBasePageCreate,
    KnowledgeBasePagePublic,
    KnowledgeBasePageUpdate,
    KnowledgeBasePageVersion,
)
from app.models.user import User
```

Add helpers near the top:

```python
SNAPSHOT_FIELDS = ("title", "summary", "tags", "content")


def page_snapshot(page: KnowledgeBasePage) -> dict:
    return {
        "title": page.title,
        "summary": page.summary,
        "tags": list(page.tags),
        "content": page.content,
    }


def changed_fields(before: dict | None, after: dict) -> list[str]:
    if before is None:
        return list(SNAPSHOT_FIELDS)
    return [field for field in SNAPSHOT_FIELDS if before.get(field) != after.get(field)]


async def next_version_number(session: AsyncSession, page_id: int) -> int:
    result = await session.execute(
        select(func.max(KnowledgeBasePageVersion.version_number)).where(
            KnowledgeBasePageVersion.page_id == page_id
        )
    )
    current = result.scalar_one_or_none()
    return int(current or 0) + 1


async def record_version(
    session: AsyncSession,
    page: KnowledgeBasePage,
    *,
    action: str,
    actor: User,
    before: dict | None = None,
    reverted_from_version_id: int | None = None,
) -> KnowledgeBasePageVersion:
    after = page_snapshot(page)
    version = KnowledgeBasePageVersion(
        page_id=page.id,
        organisation_id=page.organisation_id,
        version_number=await next_version_number(session, page.id),
        action=action,
        snapshot=after,
        changed_fields=changed_fields(before, after),
        edited_by=str(actor.id),
        edited_by_email=actor.email,
        reverted_from_version_id=reverted_from_version_id,
        created_by=str(actor.id),
    )
    session.add(version)
    await session.flush()
    return version
```

Update `create_page` signature and body:

```python
async def create_page(
    session: AsyncSession,
    page_in: KnowledgeBasePageCreate,
    *,
    organisation_id: str,
    actor: User,
    action: str = "create",
) -> KnowledgeBasePage:
    page = KnowledgeBasePage(
        organisation_id=organisation_id,
        title=page_in.title,
        summary=page_in.summary,
        tags=page_in.tags,
        content=page_in.content,
        created_by=str(actor.id),
    )
    session.add(page)
    await session.flush()
    await record_version(session, page, action=action, actor=actor)
    return page
```

Update `update_page`:

```python
async def update_page(
    session: AsyncSession,
    page: KnowledgeBasePage,
    page_in: KnowledgeBasePageUpdate,
    actor: User,
) -> KnowledgeBasePage:
    before = page_snapshot(page)
    update_data = page_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(page, k, v)
    page.updated_at = datetime.now(UTC).replace(tzinfo=None)
    page.updated_by = str(actor.id)
    session.add(page)
    await session.flush()
    await record_version(session, page, action="update", actor=actor, before=before)
    return page
```

Add list helpers:

```python
async def list_versions(
    session: AsyncSession, page_id: int, organisation_id: str
) -> list[KnowledgeBasePageVersion]:
    result = await session.execute(
        select(KnowledgeBasePageVersion)
        .where(
            KnowledgeBasePageVersion.page_id == page_id,
            KnowledgeBasePageVersion.organisation_id == organisation_id,
        )
        .order_by(desc(KnowledgeBasePageVersion.version_number))
    )
    return list(result.scalars().all())


async def contributors_for_pages(
    session: AsyncSession, page_ids: Iterable[int]
) -> dict[int, list[KnowledgeBaseContributor]]:
    ids = list(page_ids)
    if not ids:
        return {}
    versions = await session.execute(
        select(KnowledgeBasePageVersion)
        .where(KnowledgeBasePageVersion.page_id.in_(ids))
        .order_by(desc(KnowledgeBasePageVersion.edited_at))
    )
    contributors: dict[int, list[KnowledgeBaseContributor]] = {page_id: [] for page_id in ids}
    seen: dict[int, set[str]] = {page_id: set() for page_id in ids}
    for version in versions.scalars().all():
        if version.edited_by in seen[version.page_id]:
            continue
        seen[version.page_id].add(version.edited_by)
        contributors[version.page_id].append(
            KnowledgeBaseContributor(
                id=version.edited_by,
                email=version.edited_by_email,
                last_edited_at=version.edited_at,
            )
        )
    return contributors
```

Add a presenter helper:

```python
async def public_page(
    session: AsyncSession, page: KnowledgeBasePage
) -> KnowledgeBasePagePublic:
    contributors = (await contributors_for_pages(session, [page.id])).get(page.id, [])
    base = KnowledgeBasePagePublic.model_validate(page, from_attributes=True)
    return base.model_copy(
        update={
            "contributors": contributors,
            "last_edited_by": contributors[0] if contributors else None,
        },
    )
```

- [ ] **Step 4: Wire routes to actor-based CRUD and add versions endpoint**

In `app/api/v1/routes/knowledge_bases.py`, import version public schema:

```python
    KnowledgeBasePageVersionPublic,
```

Update create and update calls:

```python
    page = await kb_crud.create_page(
        session,
        page_in,
        organisation_id=ctx.organisation_id,
        actor=ctx.user,
    )
    return await kb_crud.public_page(session, page)
```

```python
    page = await kb_crud.update_page(session, page, page_in, actor=ctx.user)
    return await kb_crud.public_page(session, page)
```

Update list response:

```python
    public_pages = [await kb_crud.public_page(session, p) for p in pages]
    return Page(items=public_pages, total=total, skip=skip, limit=limit)
```

Add this endpoint:

```python
@router.get("/{page_id}/versions", response_model=list[KnowledgeBasePageVersionPublic])
async def list_kb_page_versions(
    page_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KnowledgeBasePageVersionPublic]:
    _require_perm(ctx, "read:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    versions = await kb_crud.list_versions(session, page_id, ctx.organisation_id)
    return [
        KnowledgeBasePageVersionPublic.model_validate(v, from_attributes=True)
        for v in versions
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_create_and_update_record_versions_and_contributors tests/test_api_blocked_features.py::test_kb_readonly_user_cannot_mutate_history_actions -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-api
git add app/crud/knowledge_base.py app/api/v1/routes/knowledge_bases.py tests/test_api_blocked_features.py
git commit -m "feat: track kb edit versions"
```

---

### Task 3: API Revert, Export, And Import

**Files:**
- Modify: `/Users/local/catlico/catlico-api/app/crud/knowledge_base.py`
- Modify: `/Users/local/catlico/catlico-api/app/api/v1/routes/knowledge_bases.py`
- Test: `/Users/local/catlico/catlico-api/tests/test_api_blocked_features.py`

- [ ] **Step 1: Write failing tests for revert, export, import, and cross-org safety**

Add these tests after the Task 2 tests:

```python
async def test_kb_revert_restores_snapshot_and_records_revert_version(
    client: AsyncClient, org_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Runbook", "summary": "v1", "tags": ["one"], "content": "v1"},
        headers=h,
    )
    page_id = created.json()["id"]
    await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"summary": "v2", "tags": ["two"], "content": "v2"},
        headers=h,
    )
    versions = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)).json()
    create_version = next(v for v in versions if v["action"] == "create")

    reverted = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{create_version['id']}/revert",
        headers=h,
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["summary"] == "v1"
    assert reverted.json()["tags"] == ["one"]
    assert reverted.json()["content"] == "v1"

    history = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)).json()
    assert history[0]["action"] == "revert"
    assert history[0]["reverted_from_version_id"] == create_version["id"]
    assert history[0]["snapshot"]["content"] == "v1"


async def test_kb_export_and_import_json_document(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={
            "title": "Exported",
            "summary": "Portable",
            "tags": ["portable"],
            "content": "Carry me",
        },
        headers=h,
    )
    page_id = created.json()["id"]

    exported = await client.get(f"/api/v1/knowledge-base/{page_id}/export", headers=h)
    assert exported.status_code == 200, exported.text
    document = exported.json()
    assert document["page"]["title"] == "Exported"
    assert document["versions"][0]["action"] == "create"

    imported = await client.post(
        "/api/v1/knowledge-base/import",
        json=document,
        headers=h,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["id"] != page_id
    assert imported.json()["title"] == "Exported"
    assert imported.json()["content"] == "Carry me"

    imported_history = await client.get(
        f"/api/v1/knowledge-base/{imported.json()['id']}/versions",
        headers=h,
    )
    assert imported_history.json()[0]["action"] == "import"


async def test_kb_cross_org_cannot_export_or_revert(
    client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token
):
    ha = _h(analyst_a_token, org_a.id)
    hb = _h(analyst_b_token, org_b.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Org A", "content": "private"},
        headers=ha,
    )
    page_id = created.json()["id"]
    versions = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=ha)).json()

    export = await client.get(f"/api/v1/knowledge-base/{page_id}/export", headers=hb)
    assert export.status_code == 404

    revert = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{versions[0]['id']}/revert",
        headers=hb,
    )
    assert revert.status_code == 404
```

Extend `test_kb_readonly_user_cannot_mutate_history_actions` with:

```python
    versions = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=writer_h)).json()
    revert = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{versions[0]['id']}/revert",
        headers=reader_h,
    )
    assert revert.status_code == 403

    imported = await client.post(
        "/api/v1/knowledge-base/import",
        json={
            "page": {"title": "Reader import", "content": "blocked"},
            "versions": [],
        },
        headers=reader_h,
    )
    assert imported.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_revert_restores_snapshot_and_records_revert_version tests/test_api_blocked_features.py::test_kb_export_and_import_json_document tests/test_api_blocked_features.py::test_kb_cross_org_cannot_export_or_revert tests/test_api_blocked_features.py::test_kb_readonly_user_cannot_mutate_history_actions -q
```

Expected: FAIL because revert/export/import endpoints are not implemented.

- [ ] **Step 3: Implement CRUD helpers**

In `app/crud/knowledge_base.py`, add:

```python
async def get_version(
    session: AsyncSession,
    *,
    page_id: int,
    version_id: int,
    organisation_id: str,
) -> KnowledgeBasePageVersion | None:
    result = await session.execute(
        select(KnowledgeBasePageVersion).where(
            KnowledgeBasePageVersion.id == version_id,
            KnowledgeBasePageVersion.page_id == page_id,
            KnowledgeBasePageVersion.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def revert_page(
    session: AsyncSession,
    page: KnowledgeBasePage,
    version: KnowledgeBasePageVersion,
    actor: User,
) -> KnowledgeBasePage:
    before = page_snapshot(page)
    snapshot = version.snapshot
    page.title = snapshot["title"]
    page.summary = snapshot.get("summary", "")
    page.tags = list(snapshot.get("tags", []))
    page.content = snapshot.get("content", "")
    page.updated_at = datetime.now(UTC).replace(tzinfo=None)
    page.updated_by = str(actor.id)
    session.add(page)
    await session.flush()
    await record_version(
        session,
        page,
        action="revert",
        actor=actor,
        before=before,
        reverted_from_version_id=version.id,
    )
    return page


async def export_page(
    session: AsyncSession, page: KnowledgeBasePage
) -> KnowledgeBasePageExport:
    return KnowledgeBasePageExport(
        page=await public_page(session, page),
        versions=[
            KnowledgeBasePageVersionPublic.model_validate(v, from_attributes=True)
            for v in await list_versions(session, page.id, page.organisation_id)
        ],
    )


async def import_page(
    session: AsyncSession,
    document: KnowledgeBasePageImport,
    *,
    organisation_id: str,
    actor: User,
) -> KnowledgeBasePage:
    page = await create_page(
        session,
        document.page,
        organisation_id=organisation_id,
        actor=actor,
        action="import",
    )
    return page
```

Add the imports used by those helpers:

```python
    KnowledgeBasePageExport,
    KnowledgeBasePageImport,
    KnowledgeBasePagePublic,
    KnowledgeBasePageVersionPublic,
```

- [ ] **Step 4: Add routes**

In `app/api/v1/routes/knowledge_bases.py`, import:

```python
    KnowledgeBasePageExport,
    KnowledgeBasePageImport,
```

Add these endpoints before `@router.delete("/{page_id}"...)` so `import` and `export` are not captured as page ids:

```python
@router.post(
    "/import",
    response_model=KnowledgeBasePagePublic,
    status_code=status.HTTP_201_CREATED,
)
async def import_kb_page(
    document: KnowledgeBasePageImport,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.import_page(
        session,
        document,
        organisation_id=ctx.organisation_id,
        actor=ctx.user,
    )
    return await kb_crud.public_page(session, page)


@router.get("/{page_id}/export", response_model=KnowledgeBasePageExport)
async def export_kb_page(
    page_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePageExport:
    _require_perm(ctx, "read:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    return await kb_crud.export_page(session, page)


@router.post("/{page_id}/versions/{version_id}/revert", response_model=KnowledgeBasePagePublic)
async def revert_kb_page(
    page_id: int,
    version_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    version = await kb_crud.get_version(
        session,
        page_id=page_id,
        version_id=version_id,
        organisation_id=ctx.organisation_id,
    )
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base version not found"
        )
    page = await kb_crud.revert_page(session, page, version, actor=ctx.user)
    return await kb_crud.public_page(session, page)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_revert_restores_snapshot_and_records_revert_version tests/test_api_blocked_features.py::test_kb_export_and_import_json_document tests/test_api_blocked_features.py::test_kb_cross_org_cannot_export_or_revert tests/test_api_blocked_features.py::test_kb_readonly_user_cannot_mutate_history_actions -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-api
git add app/crud/knowledge_base.py app/api/v1/routes/knowledge_bases.py tests/test_api_blocked_features.py
git commit -m "feat: add kb revert import export"
```

---

### Task 4: Web Query Contracts

**Files:**
- Modify: `/Users/local/catlico/catlico-web/src/components/KnowledgeBase/knowledgeBaseQueries.ts`
- Modify: `/Users/local/catlico/catlico-web/src/components/pages/knowledge-base/model.ts`
- Test: `/Users/local/catlico/catlico-web/tests/components/pages/KnowledgeBasePage.test.tsx`

- [ ] **Step 1: Write failing web tests for contributor display and history action**

Update `pageDto` in `tests/components/pages/KnowledgeBasePage.test.tsx`:

```ts
const pageDto = {
  id: 1,
  title: 'Phishing response runbook',
  summary: 'Standard procedure for phishing.',
  tags: ['runbook', 'phishing'],
  content: '## Triage\n\n- Pull .eml\n- Capture headers',
  organisation_id: 'origin-soc',
  created_by: 'P. Nguyen',
  created_at: '2026-06-20T00:00:00Z',
  updated_at: '2026-06-21T00:00:00Z',
  contributors: [
    {
      id: 'user-1',
      email: 'analyst@example.com',
      last_edited_at: '2026-06-21T00:00:00Z',
    },
  ],
  last_edited_by: {
    id: 'user-1',
    email: 'analyst@example.com',
    last_edited_at: '2026-06-21T00:00:00Z',
  },
}
```

Also add empty contributor metadata to `pageDto2`:

```ts
const pageDto2 = {
  id: 2,
  title: 'BEC investigation guide',
  summary: 'For confirmed BEC.',
  tags: ['bec'],
  content: 'Start here.',
  organisation_id: 'origin-soc',
  created_by: 'A. Whitford',
  created_at: '2026-06-19T00:00:00Z',
  updated_at: null,
  contributors: [],
  last_edited_by: null,
}
```

Add this test:

```ts
test('renders contributors in the detail header and fetches history from the action menu', async () => {
  vi.mocked(api.get).mockImplementation((input: string) => {
    if (String(input) === 'knowledge-base/1/versions') {
      return {
        json: async () => [
          {
            id: 10,
            page_id: 1,
            version_number: 1,
            action: 'create',
            snapshot: pageDto,
            changed_fields: ['title', 'summary', 'tags', 'content'],
            edited_by: 'user-1',
            edited_by_email: 'analyst@example.com',
            edited_at: '2026-06-21T00:00:00Z',
            reverted_from_version_id: null,
          },
        ],
      } as ReturnType<typeof api.get>
    }
    return {
      json: async () => ({ items: [pageDto, pageDto2], total: 2, skip: 0, limit: 100 }),
    } as ReturnType<typeof api.get>
  })

  render(<Harness />)
  await waitForPageList()

  expect(screen.getByText(/Edited by analyst@example.com/)).toBeDefined()
  expect(screen.getByText(/Contributors: analyst@example.com/)).toBeDefined()

  fireEvent.click(screen.getByRole('button', { name: /page actions/i }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /history/i }))

  await screen.findByText(/Version 1/)
  expect(api.get).toHaveBeenCalledWith('knowledge-base/1/versions')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: FAIL because contributors and history UI are not implemented.

- [ ] **Step 3: Add web types and API functions**

In `src/components/KnowledgeBase/knowledgeBaseQueries.ts`, add:

```ts
export type KnowledgeBaseContributor = {
  id: string
  email: string
  last_edited_at: string
}

export type KnowledgeBasePageVersionPublic = {
  id: number
  page_id: number
  version_number: number
  action: 'create' | 'update' | 'revert' | 'import'
  snapshot: {
    title?: string
    summary?: string
    tags?: string[]
    content?: string
  }
  changed_fields: string[]
  edited_by: string
  edited_by_email: string
  edited_at: string
  reverted_from_version_id: number | null
}

export type KnowledgeBasePageExport = {
  page: KnowledgeBasePagePublic
  versions: KnowledgeBasePageVersionPublic[]
}
```

Update `KnowledgeBasePagePublic`:

```ts
  contributors: KnowledgeBaseContributor[]
  last_edited_by: KnowledgeBaseContributor | null
```

Add query keys:

```ts
  versions: (id: number) => [...kbKeys.detail(id), 'versions'] as const,
```

Add functions:

```ts
export async function fetchKnowledgeBasePageVersions(
  id: number,
): Promise<KnowledgeBasePageVersionPublic[]> {
  return api.get(`knowledge-base/${id}/versions`).json<KnowledgeBasePageVersionPublic[]>()
}

export async function revertKnowledgeBasePage(
  pageId: number,
  versionId: number,
): Promise<KnowledgeBasePagePublic> {
  return api
    .post(`knowledge-base/${pageId}/versions/${versionId}/revert`)
    .json<KnowledgeBasePagePublic>()
}

export async function exportKnowledgeBasePage(
  id: number,
): Promise<KnowledgeBasePageExport> {
  return api.get(`knowledge-base/${id}/export`).json<KnowledgeBasePageExport>()
}

export async function importKnowledgeBasePage(
  document: KnowledgeBasePageExport,
): Promise<KnowledgeBasePagePublic> {
  return api
    .post('knowledge-base/import', { json: document })
    .json<KnowledgeBasePagePublic>()
}
```

In `src/components/pages/knowledge-base/model.ts`, extend `KBPage`:

```ts
  contributors: { id: string; email: string; lastEditedAt: string }[]
  lastEditedBy: { id: string; email: string; lastEditedAt: string } | null
```

Update `fromApi`:

```ts
    contributors: p.contributors.map((contributor) => ({
      id: contributor.id,
      email: contributor.email,
      lastEditedAt: contributor.last_edited_at,
    })),
    lastEditedBy: p.last_edited_by
      ? {
          id: p.last_edited_by.id,
          email: p.last_edited_by.email,
          lastEditedAt: p.last_edited_by.last_edited_at,
        }
      : null,
```

- [ ] **Step 4: Implement contributor and history UI**

In `src/components/pages/KnowledgeBasePage.tsx`, import:

```ts
  Drawer,
  FileButton,
  Divider,
```

Import query helpers:

```ts
  exportKnowledgeBasePage,
  fetchKnowledgeBasePageVersions,
  importKnowledgeBasePage,
  revertKnowledgeBasePage,
```

Import icons:

```ts
import { Download, History, MoreHorizontal, Pencil, RotateCcw, Trash2, Upload } from 'lucide-react'
```

Add state:

```ts
  const [historyOpen, setHistoryOpen] = useState(false)
```

Add versions query:

```ts
  const versionsQuery = useQuery({
    queryKey: selectedPage ? kbKeys.versions(selectedPage.id) : [...kbKeys.all, 'versions', 'none'],
    queryFn: () => {
      if (!selectedPage) return Promise.resolve([])
      return fetchKnowledgeBasePageVersions(selectedPage.id)
    },
    enabled: historyOpen && Boolean(selectedPage),
  })
```

Add detail metadata below tags:

```tsx
                  {selectedPage.lastEditedBy && (
                    <Text c="dimmed" size="xs">
                      Edited by {selectedPage.lastEditedBy.email}
                    </Text>
                  )}
                  {selectedPage.contributors.length > 0 && (
                    <Text c="dimmed" size="xs">
                      Contributors: {selectedPage.contributors.map((c) => c.email).join(', ')}
                    </Text>
                  )}
```

Add `History` menu item above `Edit`:

```tsx
                        <Menu.Item
                          leftSection={<History size={14} />}
                          onClick={() => setHistoryOpen(true)}
                        >
                          History
                        </Menu.Item>
```

Add a drawer below the tabs:

```tsx
      <Drawer
        opened={historyOpen}
        onClose={() => setHistoryOpen(false)}
        title="Version history"
        position="right"
        size="md"
      >
        <Stack gap="sm">
          {versionsQuery.isPending ? (
            <Text c="dimmed">Loading history...</Text>
          ) : versionsQuery.data?.length ? (
            versionsQuery.data.map((version) => (
              <Paper key={version.id} withBorder p="sm" radius="sm">
                <Group justify="space-between" align="flex-start">
                  <Stack gap={2}>
                    <Text fw={700}>Version {version.version_number}</Text>
                    <Text size="sm" c="dimmed">
                      {version.action} by {version.edited_by_email}
                    </Text>
                    <Text size="xs" c="dimmed">
                      Changed: {version.changed_fields.join(', ') || 'no fields'}
                    </Text>
                  </Stack>
                </Group>
              </Paper>
            ))
          ) : (
            <Text c="dimmed">No version history yet.</Text>
          )}
        </Stack>
      </Drawer>
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: PASS for the new test and existing KB tests.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-web
git add src/components/KnowledgeBase/knowledgeBaseQueries.ts src/components/pages/knowledge-base/model.ts src/components/pages/KnowledgeBasePage.tsx tests/components/pages/KnowledgeBasePage.test.tsx
git commit -m "feat: show kb contributors and history"
```

---

### Task 5: Web Revert, Export, And Import UI

**Files:**
- Modify: `/Users/local/catlico/catlico-web/src/components/pages/KnowledgeBasePage.tsx`
- Modify: `/Users/local/catlico/catlico-web/tests/components/pages/KnowledgeBasePage.test.tsx`

- [ ] **Step 1: Write failing web tests for revert, export, and import**

Add these tests:

```ts
test('reverts a selected history version and refreshes the knowledge base', async () => {
  vi.mocked(api.get).mockImplementation((input: string) => {
    if (String(input) === 'knowledge-base/1/versions') {
      return {
        json: async () => [
          {
            id: 10,
            page_id: 1,
            version_number: 1,
            action: 'create',
            snapshot: pageDto,
            changed_fields: ['content'],
            edited_by: 'user-1',
            edited_by_email: 'analyst@example.com',
            edited_at: '2026-06-21T00:00:00Z',
            reverted_from_version_id: null,
          },
        ],
      } as ReturnType<typeof api.get>
    }
    return {
      json: async () => ({ items: [pageDto, pageDto2], total: 2, skip: 0, limit: 100 }),
    } as ReturnType<typeof api.get>
  })
  vi.mocked(api.post).mockReturnValue({
    json: async () => pageDto,
  } as ReturnType<typeof api.post>)

  render(<Harness />)
  await waitForPageList()
  fireEvent.click(screen.getByRole('button', { name: /page actions/i }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /history/i }))
  fireEvent.click(await screen.findByRole('button', { name: /revert version 1/i }))

  await waitFor(() => {
    expect(api.post).toHaveBeenCalledWith('knowledge-base/1/versions/10/revert')
  })
})


test('exports the selected page as json from the action menu', async () => {
  const createObjectURL = vi.fn(() => 'blob:kb')
  const revokeObjectURL = vi.fn()
  URL.createObjectURL = createObjectURL
  URL.revokeObjectURL = revokeObjectURL

  vi.mocked(api.get).mockImplementation((input: string) => {
    if (String(input) === 'knowledge-base/1/export') {
      return {
        json: async () => ({ page: pageDto, versions: [] }),
      } as ReturnType<typeof api.get>
    }
    return {
      json: async () => ({ items: [pageDto, pageDto2], total: 2, skip: 0, limit: 100 }),
    } as ReturnType<typeof api.get>
  })

  render(<Harness />)
  await waitForPageList()
  fireEvent.click(screen.getByRole('button', { name: /page actions/i }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /export/i }))

  await waitFor(() => {
    expect(api.get).toHaveBeenCalledWith('knowledge-base/1/export')
  })
  expect(createObjectURL).toHaveBeenCalled()
})


test('imports a page json document and navigates to the imported numeric page url', async () => {
  const imported = { ...pageDto, id: 9, title: 'Imported page' }
  vi.mocked(api.post).mockReturnValue({
    json: async () => imported,
  } as ReturnType<typeof api.post>)

  render(<Harness />)
  await waitForPageList()

  const file = new File(
    [JSON.stringify({ page: pageDto, versions: [] })],
    'kb.json',
    { type: 'application/json' },
  )
  const input = screen.getByLabelText(/import knowledge base json/i)
  fireEvent.change(input, { target: { files: [file] } })

  await waitFor(() => {
    expect(api.post).toHaveBeenCalledWith('knowledge-base/import', {
      json: { page: pageDto, versions: [] },
    })
  })
  expect(navigate).toHaveBeenCalledWith({
    to: '/knowledge-base/$pageId',
    params: { pageId: '9' },
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: FAIL because revert, export, and import UI actions are not wired.

- [ ] **Step 3: Add mutations and file handlers**

In `KnowledgeBasePage.tsx`, add mutations:

```ts
  const revertMutation = useMutation({
    mutationFn: async (versionId: number) => {
      if (!selectedPage) throw new Error('No page selected')
      return revertKnowledgeBasePage(selectedPage.id, versionId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: kbKeys.all })
      if (selectedPage) {
        queryClient.invalidateQueries({ queryKey: kbKeys.versions(selectedPage.id) })
      }
      notifications.show({ color: 'teal', message: 'Page reverted' })
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof Error ? error.message : 'Failed to revert page',
      }),
  })

  const exportMutation = useMutation({
    mutationFn: async () => {
      if (!selectedPage) throw new Error('No page selected')
      return exportKnowledgeBasePage(selectedPage.id)
    },
    onSuccess: (document) => {
      const blob = new Blob([JSON.stringify(document, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const link = window.document.createElement('a')
      link.href = url
      link.download = `${document.page.title.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}-kb.json`
      link.click()
      URL.revokeObjectURL(url)
      notifications.show({ color: 'teal', message: 'Page exported' })
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof Error ? error.message : 'Failed to export page',
      }),
  })

  const importMutation = useMutation({
    mutationFn: importKnowledgeBasePage,
    onSuccess: (imported) => {
      queryClient.invalidateQueries({ queryKey: kbKeys.all })
      void navigate({
        to: '/knowledge-base/$pageId',
        params: { pageId: String(imported.id) },
      })
      notifications.show({ color: 'teal', message: 'Page imported' })
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof Error ? error.message : 'Failed to import page',
      }),
  })
```

Add file handler:

```ts
  const importFile = async (file: File | null) => {
    if (!file) return
    try {
      const text = await file.text()
      importMutation.mutate(JSON.parse(text))
    } catch {
      notifications.show({
        color: 'red',
        message: 'Import file must be valid JSON',
      })
    }
  }
```

- [ ] **Step 4: Wire buttons and drawer revert controls**

Near `+ New page`, add an import button:

```tsx
        <FileButton onChange={importFile} accept="application/json">
          {(props) => (
            <Button
              {...props}
              aria-label="Import knowledge base JSON"
              variant="default"
              leftSection={<Upload size={16} />}
              loading={importMutation.isPending}
            >
              Import
            </Button>
          )}
        </FileButton>
```

Add export menu item:

```tsx
                        <Menu.Item
                          leftSection={<Download size={14} />}
                          onClick={() => exportMutation.mutate()}
                        >
                          Export
                        </Menu.Item>
```

In each history version card, add:

```tsx
                  <Button
                    size="xs"
                    variant="default"
                    leftSection={<RotateCcw size={14} />}
                    loading={revertMutation.isPending}
                    onClick={() => revertMutation.mutate(version.id)}
                  >
                    Revert version {version.version_number}
                  </Button>
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-web
git add src/components/pages/KnowledgeBasePage.tsx tests/components/pages/KnowledgeBasePage.test.tsx
git commit -m "feat: add kb revert import export ui"
```

---

### Task 6: Full Verification

**Files:**
- Verify API and web only.

- [ ] **Step 1: Run API targeted tests**

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_crud tests/test_api_blocked_features.py::test_kb_create_and_update_record_versions_and_contributors tests/test_api_blocked_features.py::test_kb_readonly_user_cannot_mutate_history_actions tests/test_api_blocked_features.py::test_kb_revert_restores_snapshot_and_records_revert_version tests/test_api_blocked_features.py::test_kb_export_and_import_json_document tests/test_api_blocked_features.py::test_kb_cross_org_cannot_export_or_revert tests/test_models_blocked_features.py::test_knowledge_base_page_insert tests/test_models_blocked_features.py::test_knowledge_base_page_version_insert -q
```

Expected: PASS.

- [ ] **Step 2: Run API migration and compile checks**

```bash
cd /Users/local/catlico/catlico-api
uv run alembic heads
uv run python -m compileall app alembic
```

Expected: one Alembic head, `n0d3f5a7b9c1 (head)`, and compile success.

- [ ] **Step 3: Run web targeted tests and lint**

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx tests/components/KnowledgeBase/knowledgeBase.test.ts
pnpm lint src/components/KnowledgeBase/knowledgeBaseQueries.ts src/components/pages/KnowledgeBasePage.tsx src/components/pages/knowledge-base/model.ts tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: PASS. The existing jsdom canvas warning may appear and is acceptable if the command exits 0.

- [ ] **Step 4: Run web build**

```bash
cd /Users/local/catlico/catlico-web
pnpm build
```

Expected: PASS. The existing Vite chunk-size warning may appear and is acceptable if the command exits 0.

- [ ] **Step 5: Check running local app**

If the existing servers are still running, verify:

```bash
curl -I http://localhost:3001/knowledge-base/2
```

Expected: HTTP 200.

- [ ] **Step 6: Report dirty worktree boundaries**

Run:

```bash
cd /Users/local/catlico/catlico-api && git status --short
cd /Users/local/catlico/catlico-web && git status --short
```

Expected: only intentional KB files plus pre-existing unrelated dirty web tests remain. Do not revert unrelated files.
