"""collapse fine-grained permissions into domain groups

Revision ID: u7e0a2c4d6f8
Revises: t6d9f1b3c5e7
Create Date: 2026-07-11 00:00:00.000000

Reduces the granted permission vocabulary from 25 fine-grained strings to 10
domain groups (read/write per {investigation, intel, org, access} + run:enrichment
+ run:function). Route guards still check the fine-grained capabilities; they are
now derived from the granted group at request time (see app/models/role.py
``expand_permissions``). This migration rewrites the two places that *store*
granted permissions: ``role_permission.permission`` and ``api_key.scopes``.

The mapping is many-to-one, so it de-duplicates (a role with read:case + read:task
collapses to a single read:investigation row). It is therefore not cleanly
reversible; ``downgrade`` expands each group back to its full capability set, which
may broaden a role that previously held only part of a group.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u7e0a2c4d6f8"
down_revision: Union[str, Sequence[str], None] = "t6d9f1b3c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# fine-grained capability -> domain group
UPGRADE_MAP: dict[str, str] = {
    "read:case": "read:investigation",
    "read:task": "read:investigation",
    "read:observable": "read:investigation",
    "read:alert": "read:investigation",
    "write:case": "write:investigation",
    "write:task": "write:investigation",
    "write:observable": "write:investigation",
    "write:alert": "write:investigation",
    "read:custom_field": "read:intel",
    "read:knowledge_base": "read:intel",
    "read:function": "read:intel",
    "write:custom_field": "write:intel",
    "write:knowledge_base": "write:intel",
    "write:function": "write:intel",
    "run:enrichment": "run:enrichment",
    "run:function": "run:function",
    "read:organisation": "read:org",
    "read:connector": "read:org",
    "write:organisation": "write:org",
    "write:connector": "write:org",
    "read:user": "read:access",
    "read:role": "read:access",
    "write:user": "write:access",
    "write:role": "write:access",
}

# domain group -> fine-grained capabilities (for downgrade)
DOWNGRADE_MAP: dict[str, set[str]] = {
    "read:investigation": {"read:case", "read:task", "read:observable", "read:alert"},
    "write:investigation": {"write:case", "write:task", "write:observable", "write:alert"},
    "read:intel": {"read:custom_field", "read:knowledge_base", "read:function"},
    "write:intel": {"write:custom_field", "write:knowledge_base", "write:function"},
    "run:enrichment": {"run:enrichment"},
    "run:function": {"run:function"},
    "read:org": {"read:organisation", "read:connector"},
    "write:org": {"write:organisation", "write:connector"},
    "read:access": {"read:user", "read:role"},
    "write:access": {"write:user", "write:role"},
}


def _load_scopes(raw: object) -> list[str]:
    """asyncpg may hand back a JSON column as a str; normalise to a list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw) or []
    return list(raw)


def _remap_role_permissions(conn, mapping) -> None:
    rows = conn.execute(sa.text("SELECT role_id, permission FROM role_permission")).fetchall()
    per_role: dict[object, set[str]] = {}
    for role_id, perm in rows:
        per_role.setdefault(role_id, set())
        if perm in mapping:
            value = mapping[perm]
            per_role[role_id].update(value if isinstance(value, set) else {value})
        else:
            per_role[role_id].add(perm)  # already-migrated / unknown: keep as-is
    conn.execute(sa.text("DELETE FROM role_permission"))
    insert = sa.text(
        "INSERT INTO role_permission (role_id, permission) VALUES (:role_id, :permission)"
    )
    for role_id, perms in per_role.items():
        for perm in sorted(perms):
            conn.execute(insert, {"role_id": role_id, "permission": perm})


def _remap_api_key_scopes(conn, mapping) -> None:
    rows = conn.execute(sa.text("SELECT id, scopes FROM api_key")).fetchall()
    update = sa.text("UPDATE api_key SET scopes = :scopes WHERE id = :id").bindparams(
        sa.bindparam("scopes", type_=sa.JSON())
    )
    for key_id, raw in rows:
        scopes = _load_scopes(raw)
        if not scopes:
            continue
        new: set[str] = set()
        for scope in scopes:
            if scope in mapping:
                value = mapping[scope]
                new.update(value if isinstance(value, set) else {value})
            else:
                new.add(scope)
        conn.execute(update, {"scopes": sorted(new), "id": key_id})


def upgrade() -> None:
    conn = op.get_bind()
    _remap_role_permissions(conn, UPGRADE_MAP)
    _remap_api_key_scopes(conn, UPGRADE_MAP)


def downgrade() -> None:
    conn = op.get_bind()
    _remap_role_permissions(conn, DOWNGRADE_MAP)
    _remap_api_key_scopes(conn, DOWNGRADE_MAP)
