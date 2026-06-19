from app.models.alert import Alert  # noqa: F401
from app.models.auth import RefreshToken  # noqa: F401
from app.models.attachment import Attachment, AttachmentLink  # noqa: F401
from app.models.audit import Audit, AuditOutbox  # noqa: F401
from app.models.case_ import Case  # noqa: F401
from app.models.case_merge import CaseMerge  # noqa: F401
from app.models.case_share import CaseShare  # noqa: F401
from app.models.case_template import CaseTemplate, CaseTemplateTask  # noqa: F401
from app.models.comment import Comment  # noqa: F401
from app.models.connector import (  # noqa: F401
    Connector,
    ConnectorSecret,
    OrgConnector,
)
from app.models.custom_field import CustomField, CustomFieldValue  # noqa: F401
from app.models.enrichment import EnrichmentJob, ReportTag  # noqa: F401
from app.models.flag import Flag  # noqa: F401
from app.models.log import Log  # noqa: F401
from app.models.observable import (  # noqa: F401
    Observable,
    ObservableShare,
    ObservableType,
)
from app.models.organisation import Organisation  # noqa: F401
from app.models.organisation_link import OrganisationLink  # noqa: F401
from app.models.organisation_member import OrganisationMember  # noqa: F401
from app.models.role import Role, RolePermission  # noqa: F401
from app.models.tag import Tag, Tagging  # noqa: F401
from app.models.task import Task  # noqa: F401
from app.models.task_share import TaskShare  # noqa: F401
from app.models.user import User  # noqa: F401

from sqlalchemy import DateTime as _DateTime  # noqa: E402
from sqlmodel import SQLModel as _SQLModel  # noqa: E402

# All timestamps in this app are UTC instants produced by datetime.now(UTC) (tz-
# aware). Force every DateTime column to be timezone-aware (Postgres timestamptz)
# so those values bind correctly — asyncpg rejects an aware value into a naive
# column. Applied once here, after every table is registered, so no field (now or
# future) can silently regress to a naive column.
for _table in _SQLModel.metadata.tables.values():
    for _col in _table.columns:
        if isinstance(_col.type, _DateTime):
            _col.type.timezone = True
