import re
from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


class Organisation(TimestampMixin, table=True):
    __tablename__ = "organisation"

    id: str = Field(primary_key=True)
    name: str
    description: str = Field(default="")


class OrganisationCreate(SQLModel):
    id: str
    name: str
    description: str = ""

    @field_validator("id")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("id must be lowercase alphanumeric and dashes, e.g. 'soc-team'")
        return v


class OrganisationPublic(SQLModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime | None


class OrganisationUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
