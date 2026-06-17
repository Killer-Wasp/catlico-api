from enum import Enum

from sqlmodel import Field

from app.models.common import TimestampMixin


class CaseSharingMode(str, Enum):
    manual = "manual"
    supervised = "supervised"
    notify = "notify"


class AutoShareMode(str, Enum):
    manual = "manual"
    auto_share = "autoShare"


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
