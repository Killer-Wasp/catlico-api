import uuid
from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class Permission(str, Enum):
    read_case = "read:case"
    write_case = "write:case"
    read_task = "read:task"
    write_task = "write:task"
    read_observable = "read:observable"
    write_observable = "write:observable"
    read_alert = "read:alert"
    write_alert = "write:alert"
    read_user = "read:user"
    write_user = "write:user"
    read_organisation = "read:organisation"
    write_organisation = "write:organisation"
    read_role = "read:role"
    write_role = "write:role"
    read_connector = "read:connector"
    write_connector = "write:connector"
    run_enrichment = "run:enrichment"
    read_custom_field = "read:custom_field"
    write_custom_field = "write:custom_field"


ORG_PERMISSIONS = {
    Permission.read_case, Permission.write_case,
    Permission.read_task, Permission.write_task,
    Permission.read_observable, Permission.write_observable,
    Permission.read_alert, Permission.write_alert,
    Permission.read_user, Permission.write_user,
    Permission.read_connector, Permission.write_connector,
    Permission.run_enrichment,
    Permission.read_custom_field, Permission.write_custom_field,
}

BUILTIN_ROLES: dict[str, set[Permission]] = {
    "org-admin": ORG_PERMISSIONS,
    "analyst": {
        Permission.read_case, Permission.write_case,
        Permission.read_task, Permission.write_task,
        Permission.read_observable, Permission.write_observable,
        Permission.read_alert, Permission.write_alert,
        Permission.read_user,
        Permission.read_connector, Permission.run_enrichment,
        Permission.read_custom_field,
    },
    "read-only": {
        Permission.read_case,
        Permission.read_task,
        Permission.read_observable,
        Permission.read_alert,
        Permission.read_user,
        Permission.read_connector,
        Permission.read_custom_field,
    },
}


class Role(TimestampMixin, table=True):
    __tablename__ = "role"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True, index=True)


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permission"

    role_id: uuid.UUID = Field(foreign_key="role.id", primary_key=True, ondelete="CASCADE")
    permission: str = Field(primary_key=True)


class RoleCreate(SQLModel):
    name: str
    permissions: list[Permission]


class RolePublic(SQLModel):
    id: uuid.UUID
    name: str
    permissions: list[str]
    created_at: datetime


class RoleUpdate(SQLModel):
    permissions: list[Permission]
