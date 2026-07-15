from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.models.role import Permission
from sqlmodel import SQLModel

router = APIRouter(prefix="/permissions", tags=["permissions"])


class PermissionInfo(SQLModel):
    #: The grantable group string stored on roles, e.g. "read:case" or "manage:org".
    key: str
    #: Display grouping for the UI matrix (one row per domain).
    domain: str
    #: read | write | delete | run — the column the checkbox sits in.
    kind: str
    label: str
    description: str


# Single source of truth for how the 10 grantable groups are presented. The `key`
# of every entry must be a `Permission` value; the assertion below guarantees the
# catalog and the enum can't drift.
PERMISSION_CATALOG: list[PermissionInfo] = [
    PermissionInfo(key="read:case", domain="Cases", kind="read",
                   label="View cases", description="Read cases."),
    PermissionInfo(key="write:case", domain="Cases", kind="write",
                   label="Edit cases", description="Create and edit cases."),
    PermissionInfo(key="delete:case", domain="Cases", kind="delete",
                   label="Delete cases", description="Delete cases."),
    PermissionInfo(key="read:task", domain="Tasks", kind="read",
                   label="View tasks", description="Read tasks."),
    PermissionInfo(key="write:task", domain="Tasks", kind="write",
                   label="Edit tasks", description="Create and edit tasks."),
    PermissionInfo(key="delete:task", domain="Tasks", kind="delete",
                   label="Delete tasks", description="Delete tasks."),
    PermissionInfo(key="read:alert", domain="Alerts", kind="read",
                   label="View alerts", description="Read alerts."),
    PermissionInfo(key="write:alert", domain="Alerts", kind="write",
                   label="Edit alerts", description="Create and edit alerts."),
    PermissionInfo(key="delete:alert", domain="Alerts", kind="delete",
                   label="Delete alerts", description="Delete alerts."),
    PermissionInfo(key="read:observable", domain="Observables", kind="read",
                   label="View observables", description="Read observables."),
    PermissionInfo(key="write:observable", domain="Observables", kind="write",
                   label="Edit observables", description="Create and edit observables."),
    PermissionInfo(key="delete:observable", domain="Observables", kind="delete",
                   label="Delete observables", description="Delete observables."),
    PermissionInfo(key="manage:users", domain="Administration", kind="manage",
                   label="Manage users & roles",
                   description="Add/remove members, assign and edit roles."),
    PermissionInfo(key="manage:org", domain="Administration", kind="manage",
                   label="Manage organisation",
                   description="Manage org profile, custom fields, knowledge base, "
                               "integrations and enrichment."),
]

assert {info.key for info in PERMISSION_CATALOG} == {p.value for p in Permission}, (
    "PERMISSION_CATALOG is out of sync with the Permission enum"
)


@router.get("/", response_model=list[PermissionInfo])
async def list_permissions(_: CurrentUser) -> list[PermissionInfo]:
    """The grantable permission catalog. Rendered by the Profiles panel and the
    API-key scope picker so the UI always offers exactly what the backend enforces."""
    return PERMISSION_CATALOG
