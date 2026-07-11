"""org-scope roles: each organisation owns its own roles

Revision ID: v8f1b3d5e7a9
Revises: u7e0a2c4d6f8
Create Date: 2026-07-11 01:00:00.000000

Roles move from a single global table (unique name) to per-organisation ownership
(unique name *within* an org). Every existing global role is copied into each org
that references it (via a member or a case share), plus the three built-ins are
ensured in every org. `organisation_member.role_id` and `case_share.role_id` are
repointed to their org's copy, and the global rows are dropped.

The name uniqueness moves from `ix_role_name` (global unique) to
`uq_role_org_name` (unique per org); `name` keeps a plain index.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v8f1b3d5e7a9"
down_revision: Union[str, Sequence[str], None] = "u7e0a2c4d6f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Canonical built-in permission sets (domain groups), so every org gets sane
# built-ins even if a global role had been edited or was absent.
BUILTINS: dict[str, set[str]] = {
    "org-admin": {
        "read:investigation", "write:investigation", "read:intel", "write:intel",
        "run:enrichment", "run:function", "read:org", "write:org",
        "read:access", "write:access",
    },
    "analyst": {
        "read:investigation", "write:investigation", "read:intel",
        "run:enrichment", "run:function", "read:org", "read:access",
    },
    "read-only": {"read:investigation", "read:intel", "read:org", "read:access"},
}


def _copy_roles_per_org(conn) -> None:
    role_rows = conn.execute(sa.text("SELECT id, name FROM role")).fetchall()
    role_name = {r[0]: r[1] for r in role_rows}

    perm_rows = conn.execute(
        sa.text("SELECT role_id, permission FROM role_permission")
    ).fetchall()
    role_perms: dict[object, set[str]] = {}
    for rid, perm in perm_rows:
        role_perms.setdefault(rid, set()).add(perm)

    # name -> permissions (names were globally unique pre-migration)
    name_perms: dict[str, set[str]] = {
        name: set(role_perms.get(rid, set())) for rid, name in role_name.items()
    }
    for bname, bperms in BUILTINS.items():
        name_perms.setdefault(bname, set(bperms))

    orgs = [r[0] for r in conn.execute(sa.text("SELECT id FROM organisation")).fetchall()]
    members = conn.execute(
        sa.text("SELECT organisation_id, role_id FROM organisation_member")
    ).fetchall()
    shares = conn.execute(
        sa.text("SELECT organisation_id, role_id FROM case_share")
    ).fetchall()

    names_by_org: dict[str, set[str]] = {o: set(BUILTINS) for o in orgs}
    for org, rid in list(members) + list(shares):
        names_by_org.setdefault(org, set(BUILTINS))
        if rid in role_name:
            names_by_org[org].add(role_name[rid])

    ins_role = sa.text(
        "INSERT INTO role (id, organisation_id, name, created_by, created_at) "
        "VALUES (:id, :org, :name, 'system', now())"
    )
    ins_perm = sa.text(
        "INSERT INTO role_permission (role_id, permission) VALUES (:rid, :perm)"
    )
    new_id: dict[tuple[str, str], uuid.UUID] = {}
    for org, names in names_by_org.items():
        for name in names:
            nid = uuid.uuid4()
            new_id[(org, name)] = nid
            conn.execute(ins_role, {"id": nid, "org": org, "name": name})
            for perm in name_perms.get(name, set()):
                conn.execute(ins_perm, {"rid": nid, "perm": perm})

    for org, rid in members:
        target = new_id.get((org, role_name.get(rid)))
        if target is not None:
            conn.execute(
                sa.text(
                    "UPDATE organisation_member SET role_id = :new "
                    "WHERE organisation_id = :org AND role_id = :old"
                ),
                {"new": target, "org": org, "old": rid},
            )
    for org, rid in shares:
        target = new_id.get((org, role_name.get(rid)))
        if target is not None:
            conn.execute(
                sa.text(
                    "UPDATE case_share SET role_id = :new "
                    "WHERE organisation_id = :org AND role_id = :old"
                ),
                {"new": target, "org": org, "old": rid},
            )

    # Drop the old global rows (their role_permission rows cascade).
    conn.execute(sa.text("DELETE FROM role WHERE organisation_id IS NULL"))


def upgrade() -> None:
    op.add_column(
        "role",
        sa.Column(
            "organisation_id",
            sa.String(),
            sa.ForeignKey("organisation.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(op.f("ix_role_organisation_id"), "role", ["organisation_id"])
    # Drop the global unique-name index so per-org duplicate names are allowed.
    op.drop_index(op.f("ix_role_name"), table_name="role")

    _copy_roles_per_org(op.get_bind())

    op.create_index(op.f("ix_role_name"), "role", ["name"])
    op.create_unique_constraint("uq_role_org_name", "role", ["organisation_id", "name"])
    op.alter_column("role", "organisation_id", nullable=False)


def _collapse_roles_to_global(conn) -> None:
    """Best-effort reverse: one global role per distinct name, repoint, drop per-org."""
    role_rows = conn.execute(sa.text("SELECT id, name FROM role")).fetchall()
    role_name = {r[0]: r[1] for r in role_rows}
    perm_rows = conn.execute(
        sa.text("SELECT role_id, permission FROM role_permission")
    ).fetchall()
    role_perms: dict[object, set[str]] = {}
    for rid, perm in perm_rows:
        role_perms.setdefault(rid, set()).add(perm)

    names = sorted(set(role_name.values()))
    global_id: dict[str, uuid.UUID] = {}
    ins_role = sa.text(
        "INSERT INTO role (id, name, created_by, created_at) "
        "VALUES (:id, :name, 'system', now())"
    )
    ins_perm = sa.text(
        "INSERT INTO role_permission (role_id, permission) VALUES (:rid, :perm)"
    )
    for name in names:
        gid = uuid.uuid4()
        global_id[name] = gid
        conn.execute(ins_role, {"id": gid, "name": name})
        # permissions from any one existing per-org role of this name
        src = next((rid for rid, n in role_name.items() if n == name), None)
        for perm in role_perms.get(src, set()):
            conn.execute(ins_perm, {"rid": gid, "perm": perm})

    for rid, name in role_name.items():
        conn.execute(
            sa.text("UPDATE organisation_member SET role_id = :new WHERE role_id = :old"),
            {"new": global_id[name], "old": rid},
        )
        conn.execute(
            sa.text("UPDATE case_share SET role_id = :new WHERE role_id = :old"),
            {"new": global_id[name], "old": rid},
        )
    conn.execute(
        sa.text("DELETE FROM role WHERE organisation_id IS NOT NULL")
    )


def downgrade() -> None:
    op.drop_constraint("uq_role_org_name", "role", type_="unique")
    op.drop_index(op.f("ix_role_name"), table_name="role")

    _collapse_roles_to_global(op.get_bind())

    op.create_index(op.f("ix_role_name"), "role", ["name"], unique=True)
    op.drop_index(op.f("ix_role_organisation_id"), table_name="role")
    op.drop_column("role", "organisation_id")
