from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.crud.pagination import paginate

from app.models.custom_field import (
    CustomField,
    CustomFieldCreate,
    CustomFieldEntityType,
    CustomFieldType,
    CustomFieldUpdate,
    CustomFieldValue,
)

# Maps a field type to the typed column on CustomFieldValue that holds it.
_VALUE_COLUMN: dict[CustomFieldType, str] = {
    CustomFieldType.string: "string_value",
    CustomFieldType.integer: "integer_value",
    CustomFieldType.float: "float_value",
    CustomFieldType.boolean: "boolean_value",
    CustomFieldType.date: "date_value",
}


# --- Definitions ---

async def get_field(
    session: AsyncSession, field_id: int, organisation_id: str
) -> CustomField | None:
    field = await session.get(CustomField, field_id)
    if field is None or field.deleted_at is not None:
        return None
    if field.organisation_id != organisation_id:
        return None
    return field


async def get_field_by_name(
    session: AsyncSession, name: str, organisation_id: str
) -> CustomField | None:
    result = await session.execute(
        select(CustomField).where(
            CustomField.name == name,
            CustomField.organisation_id == organisation_id,
            CustomField.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_fields(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[CustomField], int]:
    base = select(CustomField).where(
        CustomField.organisation_id == organisation_id,
        CustomField.deleted_at.is_(None),
    )
    return await paginate(session, base, CustomField.name, skip=skip, limit=limit)


async def create_field(
    session: AsyncSession,
    field_in: CustomFieldCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> CustomField:
    if field_in.options and field_in.field_type != CustomFieldType.string:
        raise ValueError("options are only valid for string custom fields")
    field = CustomField(
        name=field_in.name,
        display_name=field_in.display_name,
        description=field_in.description,
        field_type=field_in.field_type,
        options=field_in.options,
        mandatory=field_in.mandatory,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(field)
    await session.flush()
    return field


async def update_field(
    session: AsyncSession,
    field: CustomField,
    field_in: CustomFieldUpdate,
    updated_by: str,
) -> CustomField:
    update_data = field_in.model_dump(exclude_unset=True)
    if (
        update_data.get("options")
        and field.field_type != CustomFieldType.string
    ):
        raise ValueError("options are only valid for string custom fields")
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    field.sqlmodel_update(update_data)
    session.add(field)
    await session.flush()
    return field


async def delete_field(
    session: AsyncSession, field: CustomField, deleted_by: str
) -> None:
    field.deleted_at = datetime.now(UTC)
    field.deleted_by = deleted_by
    session.add(field)
    await session.flush()


# --- Values (EAV) ---

def _coerce_in(field: CustomField, raw: Any) -> tuple[str, Any]:
    """Validate `raw` against the field's type, returning (column_name, value).
    Raises ValueError on a type mismatch or an out-of-options value."""
    col = _VALUE_COLUMN[field.field_type]
    if field.field_type == CustomFieldType.string:
        if not isinstance(raw, str):
            raise ValueError(f"'{field.name}' expects a string")
        if field.options and raw not in field.options:
            raise ValueError(
                f"'{raw}' is not an allowed value for '{field.name}'"
            )
        return col, raw
    if field.field_type == CustomFieldType.boolean:
        if not isinstance(raw, bool):
            raise ValueError(f"'{field.name}' expects a boolean")
        return col, raw
    if field.field_type == CustomFieldType.integer:
        # bool is a subclass of int — reject it explicitly.
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"'{field.name}' expects an integer")
        return col, raw
    if field.field_type == CustomFieldType.float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"'{field.name}' expects a number")
        return col, float(raw)
    # date
    if isinstance(raw, datetime):
        return col, raw
    if isinstance(raw, str):
        try:
            return col, datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                f"'{field.name}' expects an ISO 8601 date/datetime"
            ) from exc
    raise ValueError(f"'{field.name}' expects an ISO 8601 date/datetime")


def _coerce_out(field: CustomField, row: CustomFieldValue) -> Any:
    return getattr(row, _VALUE_COLUMN[field.field_type])


async def values_for(
    session: AsyncSession,
    entity_type: CustomFieldEntityType,
    entity_id: str,
) -> dict[str, Any]:
    result = await session.execute(
        select(CustomField, CustomFieldValue)
        .join(CustomFieldValue, CustomFieldValue.field_id == CustomField.id)
        .where(
            CustomFieldValue.entity_type == entity_type,
            CustomFieldValue.entity_id == entity_id,
            CustomField.deleted_at.is_(None),
        )
        .order_by(CustomField.name)
    )
    return {field.name: _coerce_out(field, row) for field, row in result.all()}


async def values_for_entities(
    session: AsyncSession,
    entity_type: CustomFieldEntityType,
    entity_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Batch variant of `values_for` for list endpoints (avoids N+1). Every id in
    `entity_ids` is present in the result, mapping to {} when it has no values."""
    out: dict[str, dict[str, Any]] = {eid: {} for eid in entity_ids}
    if not entity_ids:
        return out
    result = await session.execute(
        select(CustomField, CustomFieldValue)
        .join(CustomFieldValue, CustomFieldValue.field_id == CustomField.id)
        .where(
            CustomFieldValue.entity_type == entity_type,
            CustomFieldValue.entity_id.in_(entity_ids),
            CustomField.deleted_at.is_(None),
        )
        .order_by(CustomField.name)
    )
    for field, row in result.all():
        out[row.entity_id][field.name] = _coerce_out(field, row)
    return out


async def set_values(
    session: AsyncSession,
    entity_type: CustomFieldEntityType,
    entity_id: str,
    organisation_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Replace-semantics: the entity ends up with exactly the (non-null) values given.
    Raises ValueError for an unknown field name or a value that fails validation."""
    rows: list[CustomFieldValue] = []
    for name, raw in values.items():
        field = await get_field_by_name(session, name, organisation_id)
        if field is None:
            raise ValueError(f"Unknown custom field: {name}")
        if raw is None:
            continue  # null clears the value (nothing inserted)
        col, value = _coerce_in(field, raw)
        rows.append(
            CustomFieldValue(
                field_id=field.id,
                entity_type=entity_type,
                entity_id=entity_id,
                **{col: value},
            )
        )

    await session.execute(
        delete(CustomFieldValue).where(
            CustomFieldValue.entity_type == entity_type,
            CustomFieldValue.entity_id == entity_id,
        )
    )
    for row in rows:
        session.add(row)
    await session.flush()
    return await values_for(session, entity_type, entity_id)
