import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field, SQLModel

from app.models.common import CreatedMixin, SoftDeleteMixin, TimestampMixin


class ObservableType(SQLModel, table=True):
    __tablename__ = "observable_type"

    name: str = Field(primary_key=True)
    is_attachment: bool = Field(default=False)


#: Seeded like BUILTIN_ROLES. (name, is_attachment). Admins can add more later.
BUILTIN_OBSERVABLE_TYPES: dict[str, bool] = {
    "ip": False,
    "domain": False,
    "fqdn": False,
    "url": False,
    "uri_path": False,
    "user-agent": False,
    "mail": False,
    "mail-subject": False,
    "hash": False,
    "filename": False,
    "registry": False,
    "regexp": False,
    "other": False,
    "file": True,  # attachment-backed — rejected until the blob milestone
}


class Observable(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "observable"
    __table_args__ = (
        CheckConstraint(
            "case_id IS NOT NULL OR alert_id IS NOT NULL",
            name="ck_observable_parent",
        ),
        # Within-case value dedup; partial so soft-delete frees the slot.
        Index(
            "uq_observable_case_dedup",
            "case_id",
            "observable_type",
            "data",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND case_id IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    case_id: int | None = Field(
        default=None, foreign_key="case_.id", index=True, ondelete="CASCADE"
    )
    alert_id: int | None = Field(
        default=None, foreign_key="alert.id", index=True, ondelete="CASCADE"
    )
    observable_type: str = Field(foreign_key="observable_type.name")
    data: str = Field(index=True)
    message: str = Field(default="")
    tlp: int = Field(default=2)
    ioc: bool = Field(default=False)
    sighted: bool = Field(default=False)
    ignore_similarity: bool = Field(default=False)
    # Creator org — for case observables this drives the share fan-out.
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )
    # Rolled-up verdict from enrichment report tags (B3).
    verdict: str | None = Field(default=None, index=True)


class ObservableShare(CreatedMixin, table=True):
    __tablename__ = "observable_share"

    observable_id: uuid.UUID = Field(
        foreign_key="observable.id", primary_key=True, ondelete="CASCADE"
    )
    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )


class ObservableTypeCreate(SQLModel):
    name: str
    is_attachment: bool = False


class ObservableTypePublic(SQLModel):
    name: str
    is_attachment: bool


class ObservableCreate(SQLModel):
    observable_type: str
    data: str
    message: str = ""
    tlp: int = 2
    ioc: bool = False
    sighted: bool = False
    ignore_similarity: bool = False


class ObservablePublic(SQLModel):
    id: uuid.UUID
    case_id: int | None
    alert_id: int | None
    observable_type: str
    data: str
    message: str
    tlp: int
    ioc: bool
    sighted: bool
    ignore_similarity: bool
    organisation_id: str
    verdict: str | None = None
    created_at: datetime
    updated_at: datetime | None


class ObservableUpdate(SQLModel):
    message: str | None = None
    tlp: int | None = None
    ioc: bool | None = None
    sighted: bool | None = None
    ignore_similarity: bool | None = None


class ObservableFacets(SQLModel):
    """Filterable values across the org's observables, for the filter dropdowns."""

    #: Distinct 'source' strings (#case / AL-alert / feed).
    sources: list[str] = []
