"""G2: Metric definitions and case metric values."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class Metric(TimestampMixin, table=True):
    __tablename__ = "metric"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    name: str = Field(index=True)
    description: str = Field(default="")
    data_type: str = Field(default="number")  # number | text | boolean
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))


class CaseMetricValue(SQLModel, table=True):
    __tablename__ = "case_metric_value"

    case_id: int = Field(foreign_key="case_.id", primary_key=True, ondelete="CASCADE")
    metric_id: uuid.UUID = Field(foreign_key="metric.id", primary_key=True, ondelete="CASCADE")
    value: str = Field(default="")
    updated_at: datetime | None = None


class MetricCreate(SQLModel):
    name: str
    description: str = ""
    data_type: str = "number"
    config: dict = {}


class MetricUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    data_type: str | None = None
    config: dict | None = None


class MetricPublic(SQLModel):
    id: uuid.UUID
    name: str
    description: str
    data_type: str
    organisation_id: str
    created_at: datetime


class CaseMetricUpdate(SQLModel):
    metrics: dict[str, str] = {}  # {metric_id: value}


class CaseMetricPublic(SQLModel):
    metric_id: uuid.UUID
    metric_name: str
    value: str
    updated_at: datetime | None
