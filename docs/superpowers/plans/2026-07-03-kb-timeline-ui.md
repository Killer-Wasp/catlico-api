# Knowledge Base Timeline UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a knowledge base timeline drawer that lists page versions, previews historical snapshots, and lets permitted editors revert.

**Architecture:** The API records and serves append-only `knowledge_base_page_version` snapshots, then exposes org-scoped list and revert endpoints. The web app adds typed query helpers and a Mantine drawer opened from the existing page action menu. The current inline editor remains the only way to edit the live page.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy async sessions, Pytest, TanStack Query, Mantine, Tiptap/RichTextEditor, Vitest, Testing Library.

---

## File Structure

API repo: `/Users/local/catlico/catlico-api`

- Modify `app/crud/knowledge_base.py`: create/list version snapshots, derive contributors, and revert a page to a version snapshot.
- Modify `app/api/v1/routes/knowledge_bases.py`: expose `GET /{page_id}/versions` and `POST /{page_id}/versions/{version_id}/revert`; return enriched page responses.
- Modify `tests/test_api_blocked_features.py`: cover version listing, read-only permission boundary, revert, and cross-org safety.

Web repo: `/Users/local/catlico/catlico-web`

- Modify `src/components/KnowledgeBase/knowledgeBaseQueries.ts`: add contributor/version types and fetch/revert helpers.
- Modify `src/components/pages/knowledge-base/model.ts`: map contributor metadata into `KBPage`.
- Modify `src/components/pages/KnowledgeBasePage.tsx`: add `Timeline` menu item, drawer, preview, and revert mutation.
- Modify `tests/components/pages/KnowledgeBasePage.test.tsx`: cover opening timeline, rendering versions, previewing content, and reverting.

---

### Task 1: API Version Timeline And Revert

**Files:**
- Modify: `/Users/local/catlico/catlico-api/app/crud/knowledge_base.py`
- Modify: `/Users/local/catlico/catlico-api/app/api/v1/routes/knowledge_bases.py`
- Test: `/Users/local/catlico/catlico-api/tests/test_api_blocked_features.py`

- [ ] **Step 1: Write failing API tests**

Add these tests after `test_kb_crud` in `tests/test_api_blocked_features.py`:

```python
async def test_kb_versions_list_and_revert(client: AsyncClient, org_a, analyst_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Runbook", "summary": "v1", "tags": ["one"], "content": "first"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    page_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"summary": "v2", "tags": ["two"], "content": "second"},
        headers=h,
    )
    assert updated.status_code == 200, updated.text

    history = await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)
    assert history.status_code == 200, history.text
    versions = history.json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["action"] == "update"
    assert versions[0]["changed_fields"] == ["summary", "tags", "content"]
    assert versions[0]["edited_by_email"] == analyst_a.email

    create_version = next(v for v in versions if v["action"] == "create")
    reverted = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{create_version['id']}/revert",
        headers=h,
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["summary"] == "v1"
    assert reverted.json()["tags"] == ["one"]
    assert reverted.json()["content"] == "first"

    reverted_history = (
        await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)
    ).json()
    assert reverted_history[0]["action"] == "revert"
    assert reverted_history[0]["reverted_from_version_id"] == create_version["id"]
```

Add permission and org-scope coverage:

```python
async def test_kb_readonly_can_view_versions_but_cannot_revert(
    client: AsyncClient, org_a, analyst_a_token, readonly_a_token
):
    writer_h = _h(analyst_a_token, org_a.id)
    reader_h = _h(readonly_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Protected", "content": "current"},
        headers=writer_h,
    )
    page_id = created.json()["id"]
    versions = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=reader_h)).json()
    assert len(versions) == 1

    reverted = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{versions[0]['id']}/revert",
        headers=reader_h,
    )
    assert reverted.status_code == 403


async def test_kb_versions_are_org_scoped(
    client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token
):
    ha = _h(analyst_a_token, org_a.id)
    hb = _h(analyst_b_token, org_b.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Org A page", "content": "private"},
        headers=ha,
    )
    page_id = created.json()["id"]
    version_id = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=ha)).json()[0]["id"]

    assert (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=hb)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/knowledge-base/{page_id}/versions/{version_id}/revert",
            headers=hb,
        )
    ).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_versions_list_and_revert tests/test_api_blocked_features.py::test_kb_readonly_can_view_versions_but_cannot_revert tests/test_api_blocked_features.py::test_kb_versions_are_org_scoped -q
```

Expected: FAIL because version endpoints are not implemented.

- [ ] **Step 3: Implement version CRUD helpers**

In `app/crud/knowledge_base.py`, add imports:

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

Add helpers above `get_page`:

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
    return int(result.scalar_one_or_none() or 0) + 1


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

Change `create_page` to accept `actor: User` instead of `created_by: str`, set `created_by=str(actor.id)`, and call:

```python
    await record_version(session, page, action="create", actor=actor)
```

Change `update_page` to accept `actor: User` instead of `updated_by: str`, capture `before = page_snapshot(page)` before applying changes, set `updated_by=str(actor.id)`, and call:

```python
    await record_version(session, page, action="update", actor=actor, before=before)
```

Add version and presenter helpers:

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


async def get_version(
    session: AsyncSession, page_id: int, version_id: int, organisation_id: str
) -> KnowledgeBasePageVersion | None:
    result = await session.execute(
        select(KnowledgeBasePageVersion).where(
            KnowledgeBasePageVersion.id == version_id,
            KnowledgeBasePageVersion.page_id == page_id,
            KnowledgeBasePageVersion.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def contributors_for_pages(
    session: AsyncSession, page_ids: Iterable[int]
) -> dict[int, list[KnowledgeBaseContributor]]:
    ids = list(page_ids)
    if not ids:
        return {}
    result = await session.execute(
        select(KnowledgeBasePageVersion)
        .where(KnowledgeBasePageVersion.page_id.in_(ids))
        .order_by(desc(KnowledgeBasePageVersion.edited_at))
    )
    contributors = {page_id: [] for page_id in ids}
    seen = {page_id: set() for page_id in ids}
    for version in result.scalars().all():
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


async def public_page(session: AsyncSession, page: KnowledgeBasePage) -> KnowledgeBasePagePublic:
    contributors = (await contributors_for_pages(session, [page.id])).get(page.id, [])
    base = KnowledgeBasePagePublic.model_validate(page, from_attributes=True)
    return base.model_copy(
        update={
            "contributors": contributors,
            "last_edited_by": contributors[0] if contributors else None,
        }
    )


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
```

- [ ] **Step 4: Wire API routes**

In `app/api/v1/routes/knowledge_bases.py`, import:

```python
    KnowledgeBasePageVersionPublic,
```

Update list/create/update responses to call `await kb_crud.public_page(session, page)`. For list:

```python
    return Page(
        items=[await kb_crud.public_page(session, p) for p in pages],
        total=total,
        skip=skip,
        limit=limit,
    )
```

Update create:

```python
    page = await kb_crud.create_page(
        session,
        page_in,
        organisation_id=ctx.organisation_id,
        actor=ctx.user,
    )
    return await kb_crud.public_page(session, page)
```

Update patch:

```python
    page = await kb_crud.update_page(session, page, page_in, actor=ctx.user)
    return await kb_crud.public_page(session, page)
```

Add endpoints before `delete_kb_page`:

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
        session, page_id, version_id, ctx.organisation_id
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
uv run pytest tests/test_api_blocked_features.py::test_kb_crud tests/test_api_blocked_features.py::test_kb_versions_list_and_revert tests/test_api_blocked_features.py::test_kb_readonly_can_view_versions_but_cannot_revert tests/test_api_blocked_features.py::test_kb_versions_are_org_scoped -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-api
git add app/crud/knowledge_base.py app/api/v1/routes/knowledge_bases.py tests/test_api_blocked_features.py
git commit -m "feat: add kb timeline api"
```

---

### Task 2: Web Timeline Query Contracts

**Files:**
- Modify: `/Users/local/catlico/catlico-web/src/components/KnowledgeBase/knowledgeBaseQueries.ts`
- Modify: `/Users/local/catlico/catlico-web/src/components/pages/knowledge-base/model.ts`
- Test: `/Users/local/catlico/catlico-web/tests/components/pages/KnowledgeBasePage.test.tsx`

- [ ] **Step 1: Write failing test fixture updates**

In `tests/components/pages/KnowledgeBasePage.test.tsx`, extend `pageDto`:

```ts
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
```

Extend `pageDto2`:

```ts
  contributors: [],
  last_edited_by: null,
```

Add this assertion to the existing detail rendering test:

```ts
    expect(screen.getByText(/Edited by analyst@example.com/)).toBeDefined()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: FAIL because contributor fields are not mapped/rendered yet.

- [ ] **Step 3: Add query types and helpers**

In `knowledgeBaseQueries.ts`, add:

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
    title: string
    summary: string
    tags: string[]
    content: string
  }
  changed_fields: string[]
  edited_by: string
  edited_by_email: string
  edited_at: string
  reverted_from_version_id: number | null
}
```

Update `KnowledgeBasePagePublic`:

```ts
  contributors?: KnowledgeBaseContributor[]
  last_edited_by?: KnowledgeBaseContributor | null
```

Add key and functions:

```ts
  versions: (id: number) => [...kbKeys.detail(id), 'versions'] as const,
```

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
```

In `model.ts`, extend `KBPage`:

```ts
  contributors: { id: string; email: string; lastEditedAt: string }[]
  lastEditedBy: { id: string; email: string; lastEditedAt: string } | null
```

Update `fromApi`:

```ts
    contributors: (p.contributors ?? []).map((contributor) => ({
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

- [ ] **Step 4: Render last editor metadata**

In `KnowledgeBasePage.tsx`, after tags in read mode, add:

```tsx
                  {selectedPage.lastEditedBy && (
                    <Text c="dimmed" size="xs">
                      Edited by {selectedPage.lastEditedBy.email}
                    </Text>
                  )}
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/local/catlico/catlico-web
git add src/components/KnowledgeBase/knowledgeBaseQueries.ts src/components/pages/knowledge-base/model.ts src/components/pages/KnowledgeBasePage.tsx tests/components/pages/KnowledgeBasePage.test.tsx
git commit -m "feat: add kb timeline contracts"
```

---

### Task 3: Web Timeline Drawer, Preview, And Revert

**Files:**
- Modify: `/Users/local/catlico/catlico-web/src/components/pages/KnowledgeBasePage.tsx`
- Modify: `/Users/local/catlico/catlico-web/tests/components/pages/KnowledgeBasePage.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Add this API mock inside a test using `mockImplementation`:

```ts
if (String(input) === 'knowledge-base/1/versions') {
  return {
    json: async () => [
      {
        id: 10,
        page_id: 1,
        version_number: 2,
        action: 'update',
        snapshot: {
          title: 'Phishing response runbook',
          summary: 'Updated procedure.',
          tags: ['runbook'],
          content: '## Updated timeline content',
        },
        changed_fields: ['summary', 'content'],
        edited_by: 'user-1',
        edited_by_email: 'analyst@example.com',
        edited_at: '2026-06-21T00:00:00Z',
        reverted_from_version_id: null,
      },
    ],
  } as ReturnType<typeof api.get>
}
```

Add tests:

```ts
test('opens a timeline drawer and previews a version snapshot', async () => {
  vi.mocked(api.get).mockImplementation((input: string) => {
    if (String(input) === 'knowledge-base/1/versions') {
      return {
        json: async () => [
          {
            id: 10,
            page_id: 1,
            version_number: 2,
            action: 'update',
            snapshot: {
              title: 'Phishing response runbook',
              summary: 'Updated procedure.',
              tags: ['runbook'],
              content: '## Updated timeline content',
            },
            changed_fields: ['summary', 'content'],
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
  fireEvent.click(screen.getByRole('button', { name: /page actions/i }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /timeline/i }))

  await screen.findByRole('dialog', { name: /timeline/i })
  expect(await screen.findByText(/Version 2/)).toBeDefined()
  expect(screen.getByText(/analyst@example.com/)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: /view version 2/i }))
  expect(await screen.findByText('Updated procedure.')).toBeDefined()
  expect(await screen.findByText(/Updated timeline content/)).toBeDefined()
})


test('reverts a version from the timeline drawer', async () => {
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
            edited_at: '2026-06-20T00:00:00Z',
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
  fireEvent.click(await screen.findByRole('menuitem', { name: /timeline/i }))
  fireEvent.click(await screen.findByRole('button', { name: /revert version 1/i }))

  await waitFor(() => {
    expect(api.post).toHaveBeenCalledWith('knowledge-base/1/versions/10/revert')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx
```

Expected: FAIL because no timeline menu/drawer exists yet.

- [ ] **Step 3: Implement timeline state, query, and mutation**

In `KnowledgeBasePage.tsx`, import:

```ts
  Drawer,
  Divider,
```

Import query helpers:

```ts
  fetchKnowledgeBasePageVersions,
  revertKnowledgeBasePage,
```

Import icons:

```ts
import { History, MoreHorizontal, Pencil, RotateCcw, Trash2 } from 'lucide-react'
```

Add state:

```ts
  const [timelineOpen, setTimelineOpen] = useState(false)
  const [previewVersionId, setPreviewVersionId] = useState<number | null>(null)
```

Add query:

```ts
  const versionsQuery = useQuery({
    queryKey: selectedPage ? kbKeys.versions(selectedPage.id) : [...kbKeys.all, 'versions', 'none'],
    queryFn: () => {
      if (!selectedPage) return Promise.resolve([])
      return fetchKnowledgeBasePageVersions(selectedPage.id)
    },
    enabled: timelineOpen && Boolean(selectedPage),
  })
```

Add mutation:

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
```

Compute selected preview:

```ts
  const previewVersion =
    versionsQuery.data?.find((version) => version.id === previewVersionId) ?? null
```

- [ ] **Step 4: Add menu item and drawer**

Add this menu item before `Edit`:

```tsx
                        <Menu.Item
                          leftSection={<History size={14} />}
                          onClick={() => {
                            setPreviewVersionId(null)
                            setTimelineOpen(true)
                          }}
                        >
                          Timeline
                        </Menu.Item>
```

Add drawer after the `Tabs` block:

```tsx
      <Drawer
        opened={timelineOpen}
        onClose={() => setTimelineOpen(false)}
        title="Timeline"
        position="right"
        size="lg"
      >
        <Stack gap="md">
          {versionsQuery.isPending ? (
            <Text c="dimmed">Loading timeline...</Text>
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
                  <Group gap="xs">
                    <Button
                      size="xs"
                      variant="default"
                      onClick={() => setPreviewVersionId(version.id)}
                    >
                      View version {version.version_number}
                    </Button>
                    <Button
                      size="xs"
                      variant="default"
                      leftSection={<RotateCcw size={14} />}
                      loading={revertMutation.isPending}
                      onClick={() => revertMutation.mutate(version.id)}
                    >
                      Revert version {version.version_number}
                    </Button>
                  </Group>
                </Group>
              </Paper>
            ))
          ) : (
            <Text c="dimmed">No timeline entries yet.</Text>
          )}

          {previewVersion && (
            <>
              <Divider />
              <Stack gap="sm">
                <Title order={3} fz={18}>
                  {previewVersion.snapshot.title}
                </Title>
                <Group gap={6}>
                  {previewVersion.snapshot.tags.map((tag) => (
                    <Tag key={tag} label={tag} />
                  ))}
                </Group>
                {previewVersion.snapshot.summary && (
                  <Text c="dimmed" size="sm">
                    {previewVersion.snapshot.summary}
                  </Text>
                )}
                <KnowledgeBaseContent content={previewVersion.snapshot.content} />
              </Stack>
            </>
          )}
        </Stack>
      </Drawer>
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
git commit -m "feat: add kb timeline drawer"
```

---

### Task 4: Verification

**Files:**
- Verify API and web repos.

- [ ] **Step 1: Run API checks**

```bash
cd /Users/local/catlico/catlico-api
uv run pytest tests/test_api_blocked_features.py::test_kb_crud tests/test_api_blocked_features.py::test_kb_versions_list_and_revert tests/test_api_blocked_features.py::test_kb_readonly_can_view_versions_but_cannot_revert tests/test_api_blocked_features.py::test_kb_versions_are_org_scoped tests/test_models_blocked_features.py::test_knowledge_base_page_version_insert -q
uv run alembic heads
```

Expected: tests pass and Alembic reports `n0d3f5a7b9c1 (head)`.

- [ ] **Step 2: Run web checks**

```bash
cd /Users/local/catlico/catlico-web
pnpm test tests/components/pages/KnowledgeBasePage.test.tsx tests/components/KnowledgeBase/knowledgeBase.test.ts
pnpm lint src/components/KnowledgeBase/knowledgeBaseQueries.ts src/components/pages/KnowledgeBasePage.tsx src/components/pages/knowledge-base/model.ts tests/components/pages/KnowledgeBasePage.test.tsx
pnpm build
```

Expected: commands pass. The existing jsdom canvas warning and Vite chunk-size warning are acceptable if exit code is 0.

- [ ] **Step 3: Check local app route**

If the dev server is running:

```bash
curl -I http://localhost:3001/knowledge-base/2
```

Expected: HTTP 200.

- [ ] **Step 4: Report final status**

Run:

```bash
cd /Users/local/catlico/catlico-api && git status --short
cd /Users/local/catlico/catlico-web && git status --short
```

Expected: only intentional changes or clean worktrees.
