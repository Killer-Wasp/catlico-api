# Knowledge Base Timeline UI Design

## Context

Knowledge base pages already use numeric URLs, inline rich text editing, and an append-only version-history table. The UI still needs a way to expose that history so users can understand who edited a page, preview older versions, and revert when needed.

## Goals

- Show a timeline of edits for the selected knowledge base page.
- Let users preview an older version without replacing the current page view.
- Let permitted editors revert the page to a previous version.
- Keep the existing inline edit flow for current-page edits.
- Preserve permissions: read-only users may view history but cannot revert.

## Non-Goals

- Live collaborative editing.
- Field-level visual diffs.
- Editing historical versions directly.
- Replacing the existing page action menu.

## API

Add these endpoints:

- `GET /api/v1/knowledge-base/{page_id}/versions`
  Returns page versions newest-first. Requires `read:knowledge_base`.
- `POST /api/v1/knowledge-base/{page_id}/versions/{version_id}/revert`
  Applies that version snapshot to the current page and records a new `revert` version. Requires `write:knowledge_base`.

Both endpoints must remain organisation-scoped. Unknown pages or versions return `404`. Missing write permission returns `403`.

The existing page response should expose contributor metadata when available so the detail header can show who last edited and who has contributed.

## Web UI

Add a `Timeline` item to the existing KB page action dropdown.

Clicking `Timeline` opens a right-side drawer for the selected page. The drawer shows version entries with:

- Version number.
- Action: create, update, import, or revert.
- Editor email.
- Edited timestamp.
- Changed fields.
- `View` button to preview the selected version snapshot.
- `Revert` button to restore that version.

The drawer preview shows the selected version's title, tags, summary, and rendered content. The main page remains on the current version. Existing inline `Edit` still edits the current page.

After a successful revert, the app invalidates the KB list and version-history queries, closes the preview selection only if the reverted version disappeared, and shows a success notification.

## Testing

API tests cover:

- Listing versions for a page.
- Read-only users can list versions but cannot revert.
- Revert restores snapshot data and records a new `revert` version.
- Cross-organisation users cannot list or revert versions.

Web tests cover:

- The action menu exposes `Timeline`.
- Opening the timeline fetches versions.
- The drawer renders version metadata and preview content.
- Revert calls the correct endpoint and refreshes KB data.
