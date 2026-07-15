"""Password reset email delivery.

Builds the reset link and hands the message to the shared SMTP transport
(:mod:`app.services.smtp`). If SMTP is not configured, delivery is skipped
without leaking the raw token; the public auth route always returns the same
message regardless of delivery outcome.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from app.core.configs import settings
from app.services.smtp import send_email

logger = logging.getLogger(__name__)


def build_reset_link(token: str) -> str:
    base = settings.FRONTEND_HOST.rstrip("/")
    path = settings.PASSWORD_RESET_PATH
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}?{urlencode({'token': token})}"


async def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset email. Returns False when SMTP is not configured or
    delivery fails (reset requests must not leak delivery state)."""
    if not settings.SMTP_HOST:
        return False

    link = build_reset_link(token)
    body = (
        "We received a request to reset your Catlico password.\n\n"
        f"Reset password: {link}\n\n"
        "If you did not request this, ignore this email."
    )

    try:
        await send_email([email], "Reset your Catlico password", body)
    except Exception:  # noqa: BLE001 - reset requests must not leak delivery state
        logger.warning("password reset email delivery failed", exc_info=True)
        return False
    return True


async def send_new_user_invite_email(email: str, token: str) -> bool:
    """Send the set-password invite for an admin-created account. Reuses the reset
    link + /reset-password page (the token is an ordinary reset token). Returns
    False when SMTP is not configured or delivery fails — creation must not depend
    on delivery state, mirroring send_password_reset_email."""
    if not settings.SMTP_HOST:
        return False

    link = build_reset_link(token)
    body = (
        "An administrator created a Catlico account for you.\n\n"
        f"Set your password: {link}\n\n"
        "This link expires in 1 hour. If you were not expecting this, ignore "
        "this email."
    )

    try:
        await send_email([email], "Set your Catlico password", body)
    except Exception:  # noqa: BLE001 - creation must not leak delivery state
        logger.warning("new-user invite email delivery failed", exc_info=True)
        return False
    return True
