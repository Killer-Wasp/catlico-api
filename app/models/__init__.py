from app.models.alert import Alert  # noqa: F401
from app.models.api_key import ApiKey  # noqa: F401
from app.models.auth import PasswordResetToken, RefreshToken  # noqa: F401
from app.models.attachment import (  # noqa: F401
    Attachment,
    AttachmentLink,
    ObservableAttachmentLink,
)
from app.models.audit import Audit, AuditOutbox  # noqa: F401
from app.models.case_ import Case  # noqa: F401
from app.models.case_merge import CaseMerge  # noqa: F401
from app.models.case_share import CaseShare  # noqa: F401
from app.models.case_template import CaseTemplate, CaseTemplateTask  # noqa: F401
from app.models.comment import Comment  # noqa: F401
from app.models.custom_field import CustomField, CustomFieldValue  # noqa: F401
from app.models.flag import Flag  # noqa: F401
from app.models.function import Function, FunctionRun  # noqa: F401
from app.models.knowledge_base import KnowledgeBasePage, KnowledgeBasePageVersion  # noqa: F401
from app.models.log import Log  # noqa: F401
from app.models.dashboard import Dashboard  # noqa: F401
from app.models.metric import Metric, CaseMetricValue  # noqa: F401
from app.models.misp import MispServer  # noqa: F401
from app.models.notification import (  # noqa: F401
    NotificationRule,
    Notifier,
    NotifierDelivery,
    UserNotification,
    UserNotificationPreference,
)
from app.models.observable import (  # noqa: F401
    Observable,
    ObservableShare,
    ObservableType,
)
from app.models.organisation import Organisation  # noqa: F401
from app.models.plugin_runner import PluginRunner  # noqa: F401
from app.models.plugin_runner import PluginDefinition  # noqa: F401
from app.models.plugin_runner import PluginVersion  # noqa: F401
from app.models.plugin_runner import RunnerPluginInstallation  # noqa: F401
from app.models.plugin_runner import OrgPlugin  # noqa: F401
from app.models.plugin_runner import PluginConfig  # noqa: F401
from app.models.plugin_runner import PluginRun  # noqa: F401
from app.models.plugin_runner import PluginRunFile  # noqa: F401
from app.models.plugin_runner import PluginResult  # noqa: F401
from app.models.plugin_runner import PluginProposedAction  # noqa: F401
from app.models.plugin_runner import PluginRunDaily  # noqa: F401
from app.models.pattern import Pattern, Procedure  # noqa: F401
from app.models.organisation_link import OrganisationLink  # noqa: F401
from app.models.organisation_member import OrganisationMember  # noqa: F401
from app.models.report_template import ReportTemplate  # noqa: F401
from app.models.role import Role, RolePermission  # noqa: F401
from app.models.sla import SlaPolicy  # noqa: F401
from app.models.tag import Tag, Tagging  # noqa: F401
from app.models.task import Task  # noqa: F401
from app.models.task_share import TaskShare  # noqa: F401
from app.models.user import User  # noqa: F401

from sqlalchemy import DateTime as _DateTime  # noqa: E402
from sqlalchemy import event as _event  # noqa: E402
from sqlalchemy.orm import Mapper as _Mapper  # noqa: E402
from sqlmodel import SQLModel as _SQLModel  # noqa: E402


# All timestamps in this app are tz-aware UTC instants (datetime.now(UTC) /
# common.utcnow()). Force every DateTime column to be timezone-aware (Postgres
# timestamptz) so those values bind and read back correctly.
def _force_timezone_aware() -> None:
    for _table in _SQLModel.metadata.tables.values():
        for _col in _table.columns:
            if isinstance(_col.type, _DateTime) and not _col.type.timezone:
                _col.type.timezone = True


# Apply now for every table imported above, and again after each mapper
# configuration sweep so a model registered later (imported by a crud module but
# not here — as `log` once was) can't silently regress to a naive column.
_force_timezone_aware()
_event.listen(_Mapper, "after_configured", _force_timezone_aware)
