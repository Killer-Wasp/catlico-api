"""Shared SMTP transport.

A single stdlib ``smtplib`` sender used by every outbound-email path (password
reset, email notifiers). The connection runs in a worker thread so callers stay
async; STARTTLS and login are applied per the global ``SMTP_*`` config.

Unlike the password-reset path — which chooses to skip silently when SMTP is not
configured — this helper RAISES :class:`SMTPNotConfiguredError` when
``SMTP_HOST`` is unset. Each caller decides its own unconfigured policy: password
reset guards on ``settings.SMTP_HOST`` first (skip), while notifier delivery lets
the error propagate so the delivery is recorded ``failed`` with a clear reason.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import anyio

from app.core.configs import settings


class SMTPNotConfiguredError(RuntimeError):
    """Raised by :func:`send_email` when ``SMTP_HOST`` is not configured."""


async def send_email(to: list[str], subject: str, body: str) -> None:
    """Send a plain-text email to *to* via the global SMTP config.

    Offloads the blocking SMTP conversation to a worker thread. Applies STARTTLS
    when ``SMTP_USE_TLS`` and logs in when ``SMTP_USERNAME`` is set. Raises
    :class:`SMTPNotConfiguredError` if SMTP is not configured, and propagates any
    transport error from ``smtplib``.
    """
    if not settings.SMTP_HOST:
        raise SMTPNotConfiguredError("SMTP not configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = ", ".join(to)
    msg.set_content(body)

    def _send() -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            smtp.send_message(msg)

    await anyio.to_thread.run_sync(_send)
