"""Password reset email delivery.

Small stdlib SMTP sender. If SMTP is not configured, delivery is skipped without
leaking the raw token; the public auth route always returns the same message.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

import anyio

from app.core.configs import settings

logger = logging.getLogger(__name__)


def build_reset_link(token: str) -> str:
    base = settings.FRONTEND_HOST.rstrip("/")
    path = settings.PASSWORD_RESET_PATH
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}?{urlencode({'token': token})}"


async def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset email. Returns False when SMTP is not configured."""
    if not settings.SMTP_HOST:
        return False

    link = build_reset_link(token)
    msg = EmailMessage()
    msg["Subject"] = "Reset your Catlico password"
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = email
    msg.set_content(
        "We received a request to reset your Catlico password.\n\n"
        f"Reset password: {link}\n\n"
        "If you did not request this, ignore this email."
    )

    def _send() -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            smtp.send_message(msg)

    try:
        await anyio.to_thread.run_sync(_send)
    except Exception:  # noqa: BLE001 - reset requests must not leak delivery state
        logger.warning("password reset email delivery failed", exc_info=True)
        return False
    return True
