"""Pull-based outbound EVENT FEED for machine consumers.

`GET /events?organisation_id=<org>&since=<cursor>` is the POLLING equivalent of
the live WebSocket ``/activity`` stream (see ``app/api/v1/routes/ws.py`` and
``app/services/websocket_hub.py``). It reads the durable ``audit_outbox`` and
returns the SAME stable event envelope (``build_event_envelope``) the WS/notifier
consumers emit — for exactly the same organisation, gated by exactly the same read
capability. It discloses nothing the WS feed doesn't already disclose to that org.

Cursor: ``audit_outbox.id`` (autoincrement). Poll with ``since=next_cursor``.

ROLLING WINDOW: outbox rows are pruned by the maintenance sweep (~7d retention),
so a consumer that falls further behind than the retention window will silently
skip the pruned events — this feed is best-effort catch-up, not an infinite log.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import AuthContext, _get_auth_context, oauth2_scheme
from app.core.db import get_session
from app.models.audit import AuditOutbox
from app.services.outbox_events import build_event_envelope

router = APIRouter(prefix="/events", tags=["events"])

# Hard cap on page size (req 1): a consumer may ask for less, never more.
MAX_LIMIT = 500
# The only topic this feed exposes. Mirrors the audit activity surface; guards
# against a future non-"audit" topic leaking through this endpoint (req 6).
FEED_TOPIC = "audit"
# The read capability the WS /activity gate requires (ws.py). Same gate here.
REQUIRED_CAPABILITY = "read:case"


class EventFeedResponse(BaseModel):
    events: list[dict[str, Any]]
    next_cursor: int
    has_more: bool


async def get_events_org_context(
    organisation_id: Annotated[
        str, Query(description="Organisation whose events to read.")
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str, Depends(oauth2_scheme)],
) -> AuthContext:
    """AUTH/TENANCY gate — the make-or-break.

    Mirrors the WS ``/activity`` gate but via the standard REST credential
    (Authorization: Bearer JWT *or* API key), not the WS query-param-JWT hack.
    ``_get_auth_context`` resolves the caller, enforces org membership (superadmin
    bypass consistent with the rest of the codebase; API keys are pinned to their
    own org), and yields the caller's expanded permissions in that org. A
    non-member gets 403 here. We then require the same ``read:case`` capability the
    WS activity stream checks — a member lacking it gets 403. A caller can never
    reach another org's events: the org they authenticate against IS the org whose
    events are queried below.
    """
    ctx = await _get_auth_context(session, token, organisation_id)
    if REQUIRED_CAPABILITY not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {REQUIRED_CAPABILITY}",
        )
    return ctx


@router.get("", response_model=EventFeedResponse)
async def list_events(
    ctx: Annotated[AuthContext, Depends(get_events_org_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    since: Annotated[
        int, Query(ge=0, description="Return events with audit_outbox.id > since.")
    ] = 0,
    limit: Annotated[
        int, Query(ge=1, description=f"Page size (hard-capped at {MAX_LIMIT}).")
    ] = 100,
) -> EventFeedResponse:
    """Return this organisation's events after ``since``, oldest first.

    The org filter runs at the SQL level on the JSON payload
    (``payload ->> 'organisation_id'``) together with the ``id > since`` cursor, so
    paging is correct even across pruning: rows are ordered by ``id`` ASC and the
    cursor is the largest ``id`` returned, so the consumer never skips or duplicates
    an event between pages.
    """
    capped = min(limit, MAX_LIMIT)

    # Fetch one extra row to compute has_more without a second COUNT query.
    stmt = (
        select(AuditOutbox)
        .where(
            text("audit_outbox.payload ->> 'organisation_id' = :org_id").bindparams(
                org_id=ctx.organisation_id
            )
        )
        .where(AuditOutbox.topic == FEED_TOPIC)
        .where(AuditOutbox.id > since)
        .order_by(AuditOutbox.id.asc())
        .limit(capped + 1)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    has_more = len(rows) > capped
    page = rows[:capped]

    events = [build_event_envelope(row) for row in page]
    # next_cursor advances to the last id returned; echo the input `since` when the
    # page is empty so the consumer keeps polling from the same point.
    next_cursor = page[-1].id if page else since

    return EventFeedResponse(
        events=events, next_cursor=next_cursor, has_more=has_more
    )
