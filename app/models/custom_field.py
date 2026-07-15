from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


class CustomFieldType(str, Enum):
    string = "string"
    integer = "integer"
    float = "float"
    boolean = "boolean"
    date = "date"
    url = "url"


class CustomFieldEntityType(str, Enum):
    case = "case"
    alert = "alert"


class CustomField(TimestampMixin, SoftDeleteMixin, table=True):
    """An org-scoped, admin-defined attribute that can be filled in per case/alert.

    The definition half of the EAV pair; values live in `custom_field_value`. Identity
    is (name, organisation_id). A partial unique index (deleted_at IS NULL) lets a name
    be reused after its definition is soft-deleted — mirrors the alert dedup pattern."""

    __tablename__ = "custom_field"
    __table_args__ = (
        Index(
            "uq_custom_field_name",
            "name",
            "organisation_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    display_name: str = Field(default="")
    description: str = Field(default="")
    field_type: CustomFieldType
    # Empty = free input; non-empty = allowed values (a dropdown). Only valid for `string`.
    options: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # Reserved: not enforced on case/alert create in v1.
    mandatory: bool = Field(default=False)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )


class CustomFieldValue(SQLModel, table=True):
    """A single field's value on one entity. Polymorphic over case/alert (mirrors
    Tagging). Exactly one typed column is populated, selected by the definition's
    `field_type`; identity is (field_id, entity_type, entity_id)."""

    __tablename__ = "custom_field_value"

    field_id: int = Field(
        foreign_key="custom_field.id", primary_key=True, ondelete="CASCADE"
    )
    entity_type: CustomFieldEntityType = Field(primary_key=True)
    entity_id: str = Field(primary_key=True, index=True)

    string_value: str | None = Field(default=None)
    integer_value: int | None = Field(default=None)
    float_value: float | None = Field(default=None)
    boolean_value: bool | None = Field(default=None)
    date_value: datetime | None = Field(default=None)


class CustomFieldCreate(SQLModel):
    name: str
    display_name: str = ""
    description: str = Field(default="", description=MARKDOWN_NOTE)
    field_type: CustomFieldType
    options: list[str] = []
    mandatory: bool = False


class CustomFieldUpdate(SQLModel):
    display_name: str | None = None
    description: str | None = Field(default=None, description=MARKDOWN_NOTE)
    options: list[str] | None = None
    mandatory: bool | None = None


class CustomFieldPublic(SQLModel):
    id: int
    name: str
    display_name: str
    description: str = Field(description=MARKDOWN_NOTE)
    field_type: CustomFieldType
    options: list[str]
    mandatory: bool
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None


class CustomFieldValuesSet(SQLModel):
    """Replace-semantics body for PUT /{entity}/{id}/custom-fields.

    Keys are custom field `name`s; values are the typed value (or null to clear)."""

    values: dict[str, Any]
