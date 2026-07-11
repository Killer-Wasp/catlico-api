from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.models.role import Permission
from sqlmodel import SQLModel

router = APIRouter(prefix="/permissions", tags=["permissions"])


class PermissionInfo(SQLModel):
    #: The grantable group string stored on roles / API-key scopes, e.g. "read:org".
    key: str
    #: Display grouping for the UI matrix (one row per domain).
    domain: str
    #: read | write | run — the column the checkbox sits in.
    kind: str
    label: str
    description: str


# Single source of truth for how the 10 grantable groups are presented. The `key`
# of every entry must be a `Permission` value; the assertion below guarantees the
# catalog and the enum can't drift.
PERMISSION_CATALOG: list[PermissionInfo] = [
    PermissionInfo(key="read:investigation", domain="Investigation", kind="read",
                   label="View investigations",
                   description="Read cases, tasks, observables and alerts."),
    PermissionInfo(key="write:investigation", domain="Investigation", kind="write",
                   label="Edit investigations",
                   description="Create and edit cases, tasks, observables and alerts."),
    PermissionInfo(key="read:intel", domain="Intel", kind="read",
                   label="View intel",
                   description="Read custom fields, knowledge base and function definitions."),
    PermissionInfo(key="write:intel", domain="Intel", kind="write",
                   label="Edit intel",
                   description="Manage custom fields, knowledge base and function definitions."),
    PermissionInfo(key="run:enrichment", domain="Automation", kind="run",
                   label="Run enrichment",
                   description="Trigger plugin / enrichment runs."),
    PermissionInfo(key="run:function", domain="Automation", kind="run",
                   label="Run functions",
                   description="Execute functions."),
    PermissionInfo(key="read:org", domain="Organisation", kind="read",
                   label="View organisation",
                   description="Read org profile, SLAs, notifications, API keys and integrations."),
    PermissionInfo(key="write:org", domain="Organisation", kind="write",
                   label="Manage organisation",
                   description="Manage org profile, SLAs, notifications, API keys and integrations."),
    PermissionInfo(key="read:access", domain="Access", kind="read",
                   label="View members & roles",
                   description="Read members and roles."),
    PermissionInfo(key="write:access", domain="Access", kind="write",
                   label="Manage members & roles",
                   description="Add/remove members and assign roles."),
]

assert {info.key for info in PERMISSION_CATALOG} == {p.value for p in Permission}, (
    "PERMISSION_CATALOG is out of sync with the Permission enum"
)


@router.get("/", response_model=list[PermissionInfo])
async def list_permissions(_: CurrentUser) -> list[PermissionInfo]:
    """The grantable permission catalog. Rendered by the Profiles panel and the
    API-key scope picker so the UI always offers exactly what the backend enforces."""
    return PERMISSION_CATALOG
