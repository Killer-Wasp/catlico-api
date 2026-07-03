# Knowledge Base Version History Design

## Context

Knowledge base pages are organisation-scoped records edited through the web app. They now use numeric page ids and a markdown-backed `content` field rendered with a rich text editor. The next change is to make collaboration visible and reversible while preserving role boundaries.

Read-only organisation members must not be able to edit, delete, import, or revert knowledge base pages. Users with `write:knowledge_base` may perform those actions.

## Goals

- Track every create, update, and revert as a versioned JSON snapshot.
- Show who has edited a page and who has contributed to it.
- Allow a permitted editor to revert a page to an earlier version.
- Support import and export using JSON documents that include current page data and history.
- Keep page reads simple by leaving the current page fields on `knowledge_base_page`.

## Non-Goals

- Real-time collaborative editing.
- Field-level merge conflict resolution.
- A visual diff editor.
- Granting edit rights to read-only roles.

## Data Model

Add a `knowledge_base_page_version` table.

Each row stores:

- `id`: numeric primary key.
- `page_id`: foreign key to `knowledge_base_page`.
- `organisation_id`: organisation scope for fast filtering and safety checks.
- `version_number`: monotonically increasing integer per page.
- `action`: `create`, `update`, `revert`, or `import`.
- `snapshot`: JSON with `title`, `summary`, `tags`, and `content`.
- `changed_fields`: JSON list of changed top-level fields.
- `edited_by`: user id or API key actor id string.
- `edited_by_email`: best-effort display email at edit time.
- `edited_at`: timestamp.
- `reverted_from_version_id`: nullable reference used when the action is `revert`.

The current page table remains the source for list/detail display. History is append-only and soft-deleted pages keep their history for export and audit use.

## API

Existing list/create/update/delete endpoints keep their current paths.

Add:

- `GET /api/v1/knowledge-base/{page_id}/versions`
  Returns version history newest-first.
- `POST /api/v1/knowledge-base/{page_id}/versions/{version_id}/revert`
  Applies the selected snapshot to the page and records a new `revert` version.
- `GET /api/v1/knowledge-base/{page_id}/export`
  Returns one page JSON document including current page fields and versions.
- `POST /api/v1/knowledge-base/import`
  Imports a page JSON document. The imported page gets local ids, local organisation scope, and an `import` version snapshot.

Read endpoints require `read:knowledge_base`. Mutating endpoints require `write:knowledge_base`.

Knowledge base page response objects include:

- `contributors`: distinct users from version history, sorted by most recent edit.
- `last_edited_by`: display metadata for the latest version editor.

## Web UI

The page header keeps the title at the top, tags below it, and the action menu on the right.

Add action menu items:

- `History`: opens a drawer or side panel for the selected page.
- `Export`: downloads the page JSON document.

The history UI shows version number, action, editor, timestamp, changed fields, and a `Revert` action for users who can edit. The detail header shows contributor names or emails near the tags/summary in a compact dimmed style.

Import can be added as a page-level action near `New page`, using a JSON file picker. Successful import navigates to the imported page numeric URL.

## Error Handling

- Missing read permission returns `403` on list/history/export.
- Missing write permission returns `403` on create/update/delete/import/revert.
- Unknown page or version returns `404`.
- Import rejects malformed JSON with `400`.
- Revert records a new version instead of deleting or rewriting old versions.

## Testing

API tests cover:

- A write-permitted org member can update a KB page and creates a version row.
- A read-only org member cannot update, delete, import, or revert.
- Version history includes editor metadata and changed fields.
- Revert applies an older snapshot and records a new `revert` version.
- Export includes current page fields and history.
- Import creates a local page and initial import version.
- Cross-organisation users cannot access page history or revert.

Web tests cover:

- Contributors render on the KB detail header.
- The action menu opens history.
- Revert calls the API and refreshes the selected page.
- Export calls the API and creates a JSON download.
- Import posts JSON and navigates to `/knowledge-base/{id}`.

## Open Decisions

Use a drawer for history first. If it feels too heavy after implementation, move the same content into an inline side panel without changing API or data model.
