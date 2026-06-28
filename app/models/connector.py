from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class ConnectorType(str, Enum):
    analyzer = "analyzer"
    # responder = "responder"  # reserved for a later milestone


class Verdict(str, Enum):
    info = "info"
    safe = "safe"
    suspicious = "suspicious"
    malicious = "malicious"


#: Worst-first ordering, used to roll the worst verdict up to an observable.
VERDICT_SEVERITY: dict[str, int] = {
    Verdict.info.value: 0,
    Verdict.safe.value: 1,
    Verdict.suspicious.value: 2,
    Verdict.malicious.value: 3,
}


class Connector(TimestampMixin, table=True):
    """Global connector definition, written by the analyzer at registration time.
    The catalog is platform-wide; orgs opt in via OrgConnector."""

    __tablename__ = "connector"

    name: str = Field(primary_key=True)
    display_name: str = Field(default="")
    connector_type: str = Field(default=ConnectorType.analyzer.value)
    version: str = Field(default="")
    data_types: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    description: str = Field(default="")
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON))
    available: bool = Field(default=True)
    # Worst-case runtime the connector declares at registration; drives the work
    # lease duration so slow connectors aren't re-leased mid-run. Clamped on use.
    max_runtime_seconds: int = Field(default=60)
    # Analyzer-registered rows have no human author.
    created_by: str = Field(default="analyzer")


class ConnectorSecret(SQLModel, table=True):
    """Global per-connector settings + secrets, super-admin managed. 1:1 with Connector.
    Shared by all orgs; shipped to the trusted analyzer in the work lease."""

    __tablename__ = "connector_secret"

    connector_name: str = Field(
        foreign_key="connector.name", primary_key=True, ondelete="CASCADE"
    )
    settings: dict = Field(default_factory=dict, sa_column=Column(JSON))
    secrets_encrypted: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str | None = Field(default=None)


class OrgConnector(TimestampMixin, table=True):
    """Per-org enable/disable of a connector."""

    __tablename__ = "org_connector"

    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    connector_name: str = Field(
        foreign_key="connector.name", primary_key=True, ondelete="CASCADE"
    )
    enabled: bool = Field(default=True)
    auto_run_enabled: bool = Field(default=False)
    created_by: str = Field(default="")


# --- API I/O ---


class ConnectorRegisterItem(SQLModel):
    name: str
    display_name: str = ""
    connector_type: str = ConnectorType.analyzer.value
    version: str = ""
    data_types: list[str] = []
    description: str = ""
    manifest: dict = {}
    max_runtime_seconds: int = 60


class ConnectorRegister(SQLModel):
    connectors: list[ConnectorRegisterItem]


class ConnectorPublic(SQLModel):
    name: str
    display_name: str
    connector_type: str
    version: str
    data_types: list[str]
    description: str
    manifest: dict = {}
    available: bool
    max_runtime_seconds: int = 60
    enabled: bool = False  # resolved per active org
    auto_run_enabled: bool = False  # resolved per active org
    settings: dict = {}  # non-secret only
    has_secrets: bool = False


class ConnectorConfigUpdate(SQLModel):
    settings: dict = {}
    secrets: dict = {}  # write-only; encrypted at rest, never returned
