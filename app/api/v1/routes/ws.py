"""A4: Authenticated WebSocket endpoint for live activity stream."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, status
from app.services.websocket_hub import get_hub

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/activity")
async def ws_activity(
    websocket: WebSocket,
    token: Annotated[str, Query()] = "",
    organisation_id: Annotated[str | None, Query()] = None,
):
    """Live activity stream. Auth via query-param JWT token for test ergonomics;
    production clients should use the subprotocol header where possible.

    The client must supply `organisation_id` as a query parameter to scope the
    stream to a single organisation. Cross-org visibility is enforced by the
    `get_active_org` check below.
    """
    # ponytail: re-use FastAPI's Depends for auth inside WebSocket endpoints
    # by calling the dependency functions directly
    from app.api.deps import decode_access_token, get_user_by_id

    if not token:
        # Also try Authorization header
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return

    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    async with get_session() as session:
        user = await get_user_by_id(session, payload.user_id)
        if user is None or not user.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid user")
            return

        if not organisation_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing organisation_id")
            return

        # Verify user is a member of the org
        from app.models.organisation_member import OrganisationMember
        from sqlmodel import select
        result = await session.execute(
            select(OrganisationMember).where(
                OrganisationMember.user_id == user.id,
                OrganisationMember.organisation_id == organisation_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Not a member of organisation")
            return

        hub = get_hub()
        await hub.handle(organisation_id, websocket)
