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

    read_case = "read:case"
    write_case = "write:case"
    delete_case = "delete:case"
    read_task = "read:task"
    write_task = "write:task"
    delete_task = "delete:task"
    read_alert = "read:alert"
    write_alert = "write:alert"
    delete_alert = "delete:alert"
    read_observable = "read:observable"
    write_observable = "write:observable"
    delete_observable = "delete:observable"
    manage_users = "manage:users"
    manage_org = "manage:org"


# Each grantable group expands to the fine-grained capability strings that route
# guards (`require_permission`, `require_case_permission`, ...) actually check. Any
# string not a key here passes through ``expand_permissions`` unchanged, so a raw
# capability or a plugin-only permission (e.g. "write:plugin_result") still works.
# Delete is a distinct grantable group per domain (`delete:<domain>`), separate
# from `write:<domain>`: a role can be granted edit without destroy. The DELETE
# route guards check the fine-grained `delete:*` capabilities, which only the
# delete groups expand to — a `write:*` grant no longer implies delete.
PERMISSION_GROUPS: dict[str, set[str]] = {
    # Per-entity CRUD groups each expand to just their own capability string.
    Permission.read_case.value: {"read:case"},
    Permission.write_case.value: {"write:case"},
    Permission.delete_case.value: {"delete:case"},
    Permission.read_task.value: {"read:task"},
    Permission.write_task.value: {"write:task"},
    Permission.delete_task.value: {"delete:task"},
    Permission.read_alert.value: {"read:alert"},
    Permission.write_alert.value: {"write:alert"},
    Permission.delete_alert.value: {"delete:alert"},
    Permission.read_observable.value: {"read:observable"},
    Permission.write_observable.value: {"write:observable"},
    Permission.delete_observable.value: {"delete:observable"},
    # manage:users covers the member + role admin surface.
    Permission.manage_users.value: {
        "read:user", "write:user", "delete:user",
        "read:role", "write:role", "delete:role",
    },
    # manage:org covers org profile, integrations, custom fields, knowledge base,
    # and enrichment runs — everything an org admin configures.
    Permission.manage_org.value: {
        "read:organisation", "write:organisation", "delete:organisation",
        "read:connector", "write:connector",
        "read:custom_field", "write:custom_field", "delete:custom_field",
        "read:knowledge_base", "write:knowledge_base", "delete:knowledge_base",
        "run:enrichment",
    },
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
        Permission.read_case, Permission.write_case, Permission.delete_case,
        Permission.read_task, Permission.write_task, Permission.delete_task,
        Permission.read_alert, Permission.write_alert, Permission.delete_alert,
        Permission.read_observable, Permission.write_observable,
        Permission.delete_observable,
        Permission.manage_org,
    },
    "read-only": {
        Permission.read_case,
        Permission.read_task,
        Permission.read_alert,
        Permission.read_observable,
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
    #: Built-in roles (org-admin/analyst/read-only) are a stable, protected
    #: vocabulary seeded per-org. They cannot be edited or deleted via the API, so an
    #: admin can't strip org-admin of its permissions and brick their own org.
    is_builtin: bool = Field(default=False, nullable=False)


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
    is_builtin: bool
    created_at: datetime


class RoleUpdate(SQLModel):
    permissions: list[Permission]
