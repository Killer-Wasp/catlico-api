"""move notifier destination URL into encrypted secrets (plans/phase-2 §2.6)

Revision ID: d7f1a9c3e5b8
Revises: s5c8e0f2a4b6
Create Date: 2026-07-14 00:00:00.000000

``Notifier.target`` was a PLAINTEXT column that, for webhook/slack notifiers, held
the delivery URL — and Slack incoming-webhook URLs are bearer-capability secrets.
``target`` was echoed in ``NotifierPublic`` and rendered in the UI, leaking them.

This data migration, for each webhook/slack notifier, folds the plaintext
``target`` URL into the encrypted ``secrets_encrypted`` blob under key ``"url"``
(preserving any secret already stored there) and replaces ``target`` with a
non-sensitive display label (host + short path prefix). Senders now read the URL
from the encrypted blob; ``target`` is display-only.

Encryption uses the app's Fernet key (``SECRET_ENCRYPTION_KEY``) via
``app.core.crypto``, imported lazily inside ``upgrade()`` so the migration graph
still builds without app config loaded. On a fresh/empty database the row loop is
a no-op, so the key isn't required there.

Downgrade is best-effort: if the key is available it decrypts and restores the URL
back into ``target`` and drops it from secrets; if a row can't be decrypted, its
``target`` is left as the display label (the original plaintext can't be recovered
from Fernet without the key). email/kafka notifiers are untouched in both
directions.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f1a9c3e5b8"
down_revision: Union[str, Sequence[str], None] = "s5c8e0f2a4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Only these notifier types carry a URL in `target`.
_URL_TYPES = ("webhook", "slack")


def upgrade() -> None:
    from app.services.net_guard import build_url_secret

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, target, secrets_encrypted FROM notifier "
            "WHERE type IN ('webhook', 'slack')"
        )
    ).fetchall()
    for row in rows:
        target = (row.target or "").strip()
        if not target:
            continue
        from app.core.crypto import decrypt_secrets

        # Idempotent: skip rows already migrated (url already in secrets).
        if "url" in decrypt_secrets(row.secrets_encrypted):
            continue
        label, blob = build_url_secret(target, row.secrets_encrypted)
        conn.execute(
            sa.text(
                "UPDATE notifier SET target = :label, secrets_encrypted = :blob "
                "WHERE id = :id"
            ),
            {"label": label, "blob": blob, "id": row.id},
        )


def downgrade() -> None:
    from app.core.crypto import decrypt_secrets, encrypt_secrets

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, secrets_encrypted FROM notifier "
            "WHERE type IN ('webhook', 'slack')"
        )
    ).fetchall()
    for row in rows:
        secrets = decrypt_secrets(row.secrets_encrypted)
        url = secrets.pop("url", None)
        if not url:
            # Can't recover the plaintext URL (missing/rotated key) — leave the
            # display label in `target` as documented above.
            continue
        conn.execute(
            sa.text(
                "UPDATE notifier SET target = :url, secrets_encrypted = :blob "
                "WHERE id = :id"
            ),
            {"url": url, "blob": encrypt_secrets(secrets), "id": row.id},
        )
