"""flatten RBAC to per-entity groups + drop api_key.scopes

Revision ID: h2c4e6a8b0d2
Revises: g9d1f3b5c7e9
Create Date: 2026-07-16 00:00:00.000000

Two coordinated changes for the OOS slim-down:

1. **Flatten RBAC.** The grant vocabulary moves from the coarse domain groups
   (read/write/delete per {investigation, intel, org, access} + run:enrichment) to
   a flat per-entity set: read/write/delete per {case, task, alert, observable}
   plus ``manage:users`` and ``manage:org``. Route guards still check the same
   fine-grained capabilities; they are derived from the granted group at request
   time (see app/models/role.py ``expand_permissions``). This migration rewrites
   ``role_permission.permission`` old->new.

   - ``read:investigation``  -> read:{case,task,alert,observable}
   - ``write:investigation`` -> write:{case,task,alert,observable}
   - ``delete:investigation``-> delete:{case,task,alert,observable}
   - ``read|write|delete:intel``, ``read|write|delete:org``, ``run:enrichment``
     all fold into ``manage:org``
   - ``read|write|delete:access`` fold into ``manage:users``

   The mapping is many-to-one so it de-duplicates per role (composite PK on
   role_id+permission). It is therefore not cleanly reversible.

2. **Drop ``api_key.scopes``.** API keys are now unscoped (every key grants the
   full capability set), so the column is removed.

**Irreversible downgrade.** Because the RBAC remap collapses several distinct
groups into ``manage:org`` (intel + org + enrichment) the original grants cannot
be reconstructed, and the dropped ``scopes`` data is gone. ``downgrade`` therefore
raises — restore from a backup for non-throwaway environments.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h2c4e6a8b0d2"
down_revision: Union[str, Sequence[str], None] = "g9d1f3b5c7e9"  # down_revision fixed at merge
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# old grantable group -> set of new grantable groups
UPGRADE_MAP: dict[str, set[str]] = {
    "read:investigation": {"read:case", "read:task", "read:alert", "read:observable"},
    "write:investigation": {"write:case", "write:task", "write:alert", "write:observable"},
    "delete:investigation": {"delete:case", "delete:task", "delete:alert", "delete:observable"},
    "read:intel": {"manage:org"},
    "write:intel": {"manage:org"},
    "delete:intel": {"manage:org"},
    "run:enrichment": {"manage:org"},
    "read:org": {"manage:org"},
    "write:org": {"manage:org"},
    "delete:org": {"manage:org"},
    "read:access": {"manage:users"},
    "write:access": {"manage:users"},
    "delete:access": {"manage:users"},
}


def _remap_role_permissions(conn, mapping: dict[str, set[str]]) -> None:
    rows = conn.execute(
        sa.text("SELECT role_id, permission FROM role_permission")
    ).fetchall()
    per_role: dict[object, set[str]] = {}
    for role_id, perm in rows:
        bucket = per_role.setdefault(role_id, set())
        # Unknown / already-migrated values pass through unchanged so re-running or
        # a partially-migrated DB is safe.
        bucket.update(mapping.get(perm, {perm}))
    conn.execute(sa.text("DELETE FROM role_permission"))
    insert = sa.text(
        "INSERT INTO role_permission (role_id, permission) VALUES (:role_id, :permission)"
    )
    for role_id, perms in per_role.items():
        for perm in sorted(perms):
            conn.execute(insert, {"role_id": role_id, "permission": perm})


def upgrade() -> None:
    conn = op.get_bind()
    _remap_role_permissions(conn, UPGRADE_MAP)
    op.drop_column("api_key", "scopes")


def downgrade() -> None:
    raise NotImplementedError(
        "Irreversible: the RBAC flatten collapses intel/org/enrichment grants into "
        "manage:org and drops api_key.scopes; the original grants cannot be "
        "reconstructed. Restore from a backup taken before this revision."
    )
