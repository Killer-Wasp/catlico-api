"""split delete grants out of the write groups

Revision ID: y2b4d6f8a0c2
Revises: x1a3c5e7d9b0
Create Date: 2026-07-12 01:00:00.000000

Grant separation, the breaking finale of the delete-permission split. The
enforcement layer already checks distinct ``delete:*`` capabilities on every DELETE
route; the ``write:<domain>`` grant groups used to still expand to them. Now the
grant vocabulary carries a matching ``delete:<domain>`` group per domain
(investigation / intel / org / access) and ``write:*`` no longer implies delete.

To keep this non-breaking for existing grantees, every role and API key that
currently holds a ``write:<domain>`` group is given the paired ``delete:<domain>``
group here — so current write-holders keep exactly the delete access they had. New
grants of ``write:*`` alone will not include delete going forward.

``api_key.scopes`` is a JSON array; it is edited set-based via a ``json``→``jsonb``
round-trip (``@>`` containment, ``||`` concat). NULL/empty scope arrays are skipped
naturally by the containment predicate.

Downgrade folds the delete groups back into their write groups (drops the standalone
``delete:*`` grants), i.e. it is lossy in the same way as the original collapse — a
role granted delete-without-write would lose the delete grant. Forward-only in
practice.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "y2b4d6f8a0c2"
down_revision: Union[str, Sequence[str], None] = "x1a3c5e7d9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# write group -> paired standalone delete group
_PAIRS = (
    ("write:investigation", "delete:investigation"),
    ("write:intel", "delete:intel"),
    ("write:org", "delete:org"),
    ("write:access", "delete:access"),
)


def upgrade() -> None:
    for write_grp, delete_grp in _PAIRS:
        # Roles: add the delete group to every role that holds the write group.
        op.execute(
            f"""
            INSERT INTO role_permission (role_id, permission)
            SELECT role_id, '{delete_grp}'
            FROM role_permission
            WHERE permission = '{write_grp}'
            ON CONFLICT DO NOTHING
            """
        )
        # API keys: append the delete group to any scopes array holding the write
        # group (and not already the delete group).
        op.execute(
            f"""
            UPDATE api_key
            SET scopes = (scopes::jsonb || '["{delete_grp}"]'::jsonb)::json
            WHERE scopes::jsonb @> '["{write_grp}"]'::jsonb
              AND NOT scopes::jsonb @> '["{delete_grp}"]'::jsonb
            """
        )


def downgrade() -> None:
    for _write_grp, delete_grp in _PAIRS:
        op.execute(
            f"DELETE FROM role_permission WHERE permission = '{delete_grp}'"
        )
        op.execute(
            f"""
            UPDATE api_key
            SET scopes = (scopes::jsonb - '{delete_grp}')::json
            WHERE scopes::jsonb @> '["{delete_grp}"]'::jsonb
            """
        )
