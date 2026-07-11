"""Password reset orchestration.

Two flows sit on top of the token store (`app.crud.password_reset`):

- `request_reset` — best-effort. Silent for unknown emails, inactive accounts,
  and while a recent token is still outstanding (throttle). The endpoint always
  returns the same generic response, so nothing here signals whether an email
  exists. Delivery is scheduled as a background task so the response returns
  before the SMTP round-trip — an inline send would make known-email requests
  measurably slower than unknown ones, letting response timing reveal which
  emails are registered despite the identical body.
- `perform_reset` — validate the new password, atomically claim the token, set
  the password, invalidate outstanding tokens, revoke all sessions, and audit.
  Raises `PasswordResetError` on a bad token or a password that fails the policy.
"""

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.core.security import (
    PASSWORD_POLICY_MESSAGE,
    get_password_hash,
    password_meets_policy,
)
from app.crud import password_reset as reset_crud
from app.crud.audit import record_audit
from app.crud.auth import delete_all_refresh_tokens
from app.crud.user import get_user_by_email, get_user_by_id
from app.services import password_reset_delivery


class PasswordResetError(Exception):
    """A reset could not be completed (invalid/expired token or weak password)."""


async def request_reset(
    session: AsyncSession, email: str, background_tasks: BackgroundTasks
) -> None:
    """Create a reset token for `email` and schedule delivery, if eligible.
    Never raises for the caller: unknown email, an inactive account, and an
    active throttle all no-op silently."""
    user = await get_user_by_email(session, email)
    if user is None or not user.is_active:
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
    # Sent after the response (timing side-channel — see module docstring).
    # Delivery is a no-op unless SMTP is configured and swallows its own errors.
    background_tasks.add_task(
        password_reset_delivery.send_password_reset_email, user.email, raw_token
    )


async def perform_reset(
    session: AsyncSession, raw_token: str, new_password: str
) -> None:
    """Reset the password behind `raw_token`. The policy is checked before the
    token is claimed, so a weak password leaves the single-use link usable."""
    if not password_meets_policy(new_password):
        raise PasswordResetError(PASSWORD_POLICY_MESSAGE)
    token = await reset_crud.consume_active_token(session, raw_token)
    if token is None:
        raise PasswordResetError("Invalid or expired reset token")
    user = await get_user_by_id(session, token.user_id)
    if user is None or not user.is_active:
        raise PasswordResetError("Invalid or expired reset token")

    user.hashed_password = get_password_hash(new_password)
    session.add(user)
    await reset_crud.invalidate_user_tokens(session, user.id)
    await delete_all_refresh_tokens(session, user.id)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(user.id),
        details={"event": "password_reset_completed"},
    )
