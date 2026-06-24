from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class CaseSharingMode(str, Enum):
    manual = "manual"
    supervised = "supervised"
    notify = "notify"


class AutoShareMode(str, Enum):
    manual = "manual"
    auto_share = "autoShare"


class OrganisationLinkCreate(SQLModel):
    to_org_id: str
    case_sharing: CaseSharingMode = CaseSharingMode.manual
    task_sharing: AutoShareMode = AutoShareMode.manual
    observable_sharing: AutoShareMode = AutoShareMode.manual


class OrganisationLinkUpdate(SQLModel):
    case_sharing: CaseSharingMode | None = None
    task_sharing: AutoShareMode | None = None
    observable_sharing: AutoShareMode | None = None


class OrganisationLinkPublic(SQLModel):
    from_org_id: str
    to_org_id: str
    case_sharing: CaseSharingMode
    task_sharing: AutoShareMode
    observable_sharing: AutoShareMode


class OrganisationLink(TimestampMixin, table=True):
    __tablename__ = "organisation_link"

    from_org_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    to_org_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    case_sharing: CaseSharingMode = Field(default=CaseSharingMode.manual)
    task_sharing: AutoShareMode = Field(default=AutoShareMode.manual)
    observable_sharing: AutoShareMode = Field(default=AutoShareMode.manual)
