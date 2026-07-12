import uuid
from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class Permission(str, Enum):
    """The *grant* vocabulary — the coarse, domain-grouped permissions that roles and
    API keys are granted and that admins pick in the UI. Route guards still check the
    fine-grained capability strings in ``PERMISSION_GROUPS`` below; a granted group is
    expanded to those capabilities when an ``AuthContext`` is built (see
    ``expand_permissions``). Keeping enforcement fine-grained while grants stay coarse
    means adding a new route doesn't require a new grantable permission."""

    read_investigation = "read:investigation"
    write_investigation = "write:investigation"
    delete_investigation = "delete:investigation"
    read_intel = "read:intel"
    write_intel = "write:intel"
    delete_intel = "delete:intel"
    run_enrichment = "run:enrichment"
    run_function = "run:function"
    read_org = "read:org"
    write_org = "write:org"
    delete_org = "delete:org"
    read_access = "read:access"
    write_access = "write:access"
    delete_access = "delete:access"


# Each grantable group expands to the fine-grained capability strings that route
# guards (`require_permission`, `require_case_permission`, ...) actually check. Any
# string not a key here passes through ``expand_permissions`` unchanged, so a raw
# capability or a plugin-only permission (e.g. "write:plugin_result") still works.
# Delete is a distinct grantable group per domain (`delete:<domain>`), separate
# from `write:<domain>`: a role can be granted edit without destroy. The DELETE
# route guards check the fine-grained `delete:*` capabilities, which only the
# delete groups expand to — a `write:*` grant no longer implies delete.
PERMISSION_GROUPS: dict[str, set[str]] = {
    Permission.read_investigation.value: {
        "read:case", "read:task", "read:observable", "read:alert",
    },
    Permission.write_investigation.value: {
        "write:case", "write:task", "write:observable", "write:alert",
    },
    Permission.delete_investigation.value: {
        "delete:case", "delete:task", "delete:observable", "delete:alert",
    },
    Permission.read_intel.value: {
        "read:custom_field", "read:knowledge_base", "read:function",
    },
    Permission.write_intel.value: {
        "write:custom_field", "write:knowledge_base", "write:function",
    },
    Permission.delete_intel.value: {
        "delete:custom_field", "delete:knowledge_base", "delete:function",
    },
    Permission.run_enrichment.value: {"run:enrichment"},
    Permission.run_function.value: {"run:function"},
    Permission.read_org.value: {"read:organisation", "read:connector"},
    Permission.write_org.value: {"write:organisation", "write:connector"},
    Permission.delete_org.value: {"delete:organisation"},
    Permission.read_access.value: {"read:user", "read:role"},
    Permission.write_access.value: {"write:user", "write:role"},
    Permission.delete_access.value: {"delete:user", "delete:role"},
}

#: Every group value — the full grant surface (what a superadmin holds).
ALL_GROUPS: frozenset[str] = frozenset(p.value for p in Permission)

#: Every fine-grained capability any route guard may check (union of the groups).
ALL_CAPABILITIES: frozenset[str] = frozenset().union(*PERMISSION_GROUPS.values())


def expand_permissions(granted: Iterable[str]) -> set[str]:
    """Expand granted group permissions into the fine-grained capabilities route
    guards check. Unknown strings pass through unchanged."""
    out: set[str] = set()
    for perm in granted:
        out |= PERMISSION_GROUPS.get(perm, {perm})
    return out


ORG_PERMISSIONS: set[Permission] = set(Permission)

BUILTIN_ROLES: dict[str, set[Permission]] = {
    "org-admin": set(Permission),
    "analyst": {
        Permission.read_investigation, Permission.write_investigation,
        Permission.delete_investigation,
        Permission.read_intel,
        Permission.run_enrichment, Permission.run_function,
        Permission.read_org,
        Permission.read_access,
    },
    "read-only": {
        Permission.read_investigation,
        Permission.read_intel,
        Permission.read_org,
        Permission.read_access,
    },
}


class Role(TimestampMixin, table=True):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("organisation_id", "name", name="uq_role_org_name"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    #: Roles are org-scoped: each organisation owns its own copy of the built-in
    #: roles (and any custom ones), so an admin editing a role never affects
    #: another org. Name is unique per organisation, not globally.
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    name: str = Field(index=True)


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permission"

    role_id: uuid.UUID = Field(foreign_key="role.id", primary_key=True, ondelete="CASCADE")
    permission: str = Field(primary_key=True)


class RoleCreate(SQLModel):
    name: str
    permissions: list[Permission]


class RolePublic(SQLModel):
    id: uuid.UUID
    organisation_id: str
    name: str
    permissions: list[str]
    created_at: datetime


class RoleUpdate(SQLModel):
    permissions: list[Permission]
