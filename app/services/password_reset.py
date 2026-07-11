"""Password reset orchestration.

Two flows sit on top of the token store (`app.crud.password_reset`):

- `request_reset` — best-effort. Silent for unknown emails and while a recent
  token is still outstanding (throttle). The endpoint always returns the same
  generic response, so nothing here signals whether an email exists.
- `perform_reset` — validate the token and the new password, set the password,
  consume/invalidate tokens, revoke all sessions, and audit. Raises
  `PasswordResetError` on a bad token or a password that fails the policy.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    get_password_hash,
    password_meets_policy,
)
from app.crud import password_reset as reset_crud
from app.crud.audit import record_audit
from app.crud.auth import delete_all_refresh_tokens
from app.crud.user import get_user_by_email, get_user_by_id
from app.services import password_reset_delivery

logger = logging.getLogger(__name__)


class PasswordResetError(Exception):
    """A reset could not be completed (invalid/expired token or weak password)."""


async def request_reset(session: AsyncSession, email: str) -> None:
    """Create and deliver a reset token for `email`, if eligible. Never raises for
    the caller: unknown email and an active throttle both no-op silently."""
    user = await get_user_by_email(session, email)
    if user is None:
        return
    if await reset_crud.has_recent_unused_token(
        session, user.id, settings.PASSWORD_RESET_THROTTLE_SECONDS
    ):
        return

    raw_token, _ = await reset_crud.create_token(session, user.id)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor="system",
        details={"event": "password_reset_requested"},
    )
    # Delivery is a no-op unless SMTP is configured, and swallows its own errors,
    # so a broken mailer never changes the endpoint's response.
    await password_reset_delivery.send_password_reset_email(user.email, raw_token)


async def perform_reset(
    session: AsyncSession, raw_token: str, new_password: str
) -> None:
    """Reset the password behind `raw_token`. Validates the token and the new
    password before mutating anything, so a weak password leaves the link usable."""
    token = await reset_crud.get_active_token_by_raw(session, raw_token)
    if token is None:
        raise PasswordResetError("Invalid or expired reset token")
    if not password_meets_policy(new_password):
        raise PasswordResetError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    user = await get_user_by_id(session, token.user_id)
    if user is None:
        raise PasswordResetError("Invalid or expired reset token")

    user.hashed_password = get_password_hash(new_password)
    session.add(user)
    await reset_crud.consume_token(session, token)
    await reset_crud.invalidate_user_tokens(session, user.id)
    await delete_all_refresh_tokens(session, user.id)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(user.id),
        details={"event": "password_reset_completed"},
    )
