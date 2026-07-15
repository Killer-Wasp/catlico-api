"""CRUD + built-in seeding for the org-scoped `case_status` lookup.

Replaces the old native ``casestatus`` PG enum. Every org owns its own copy of
the four built-in statuses (Open / In progress / Resolved / Duplicated); admins
can add custom statuses, hide them, and reorder them. Consumers branch on the
status *stage* (see `app.models.case_status.CaseStage`), never the label.
"""

from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.case_ import Case
from app.models.case_status import (
    CaseStage,
    CaseStatus,
    CaseStatusCreate,
    CaseStatusRef,
    CaseStatusUpdate,
)

#: Built-in statuses seeded per org, in display order. The *labels* are stable —
#: API consumers may reference them directly (e.g. filter `status~eq~Open`). The
#: old enum values map: open→Open, resolved→Resolved, duplicated→Duplicated.
BUILTIN_STATUSES: list[tuple[str, CaseStage, str]] = [
    ("Open", CaseStage.open, "#3b82f6"),
    ("In progress", CaseStage.in_progress, "#f59e0b"),
    ("Resolved", CaseStage.closed, "#10b981"),
    ("Duplicated", CaseStage.duplicated, "#6b7280"),
]


def _ref(row: CaseStatus) -> CaseStatusRef:
    return CaseStatusRef(id=row.id, label=row.label, stage=row.stage, color=row.color)


async def seed_org_builtin_statuses(
    session: AsyncSession, organisation_id: str, created_by: str = "system"
) -> dict[CaseStage, CaseStatus]:
    """Ensure an org has its four built-in statuses. Idempotent — called at org
    creation and (safely) from the migration/backfill. Returns them by stage."""
    result: dict[CaseStage, CaseStatus] = {}
    for position, (label, stage, color) in enumerate(BUILTIN_STATUSES):
        existing = (
            await session.execute(
                select(CaseStatus).where(
                    CaseStatus.organisation_id == organisation_id,
                    CaseStatus.label == label,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = CaseStatus(
                organisation_id=organisation_id,
                label=label,
                stage=stage,
                color=color,
                is_builtin=True,
                position=position,
                created_by=created_by,
            )
            session.add(existing)
            await session.flush()
        elif not existing.is_builtin:
            existing.is_builtin = True
            session.add(existing)
        result[stage] = existing
    return result


async def get_status(
    session: AsyncSession, status_id: int, organisation_id: str
) -> CaseStatus | None:
    row = await session.get(CaseStatus, status_id)
    if row is None or row.organisation_id != organisation_id:
        return None
    return row


async def get_status_by_label(
    session: AsyncSession, label: str, organisation_id: str
) -> CaseStatus | None:
    return (
        await session.execute(
            select(CaseStatus).where(
                CaseStatus.organisation_id == organisation_id,
                CaseStatus.label == label,
            )
        )
    ).scalar_one_or_none()


async def list_statuses(
    session: AsyncSession, organisation_id: str, *, include_hidden: bool = True
) -> list[CaseStatus]:
    stmt = select(CaseStatus).where(CaseStatus.organisation_id == organisation_id)
    if not include_hidden:
        stmt = stmt.where(CaseStatus.hidden.is_(False))
    stmt = stmt.order_by(CaseStatus.position, CaseStatus.id)
    return list((await session.execute(stmt)).scalars())


async def default_status(session: AsyncSession, organisation_id: str) -> CaseStatus:
    """The status a freshly-created case gets: the built-in Open (open stage).
    Seeds the built-ins on the fly if the org somehow has none."""
    row = (
        await session.execute(
            select(CaseStatus)
            .where(
                CaseStatus.organisation_id == organisation_id,
                CaseStatus.stage == CaseStage.open,
                CaseStatus.is_builtin.is_(True),
            )
            .order_by(CaseStatus.position, CaseStatus.id)
        )
    ).scalars().first()
    if row is not None:
        return row
    seeded = await seed_org_builtin_statuses(session, organisation_id)
    return seeded[CaseStage.open]


async def duplicated_status(session: AsyncSession, organisation_id: str) -> CaseStatus:
    """The built-in duplicated-stage status — the merge tombstone target."""
    row = (
        await session.execute(
            select(CaseStatus)
            .where(
                CaseStatus.organisation_id == organisation_id,
                CaseStatus.stage == CaseStage.duplicated,
                CaseStatus.is_builtin.is_(True),
            )
            .order_by(CaseStatus.position, CaseStatus.id)
        )
    ).scalars().first()
    if row is not None:
        return row
    seeded = await seed_org_builtin_statuses(session, organisation_id)
    return seeded[CaseStage.duplicated]


async def refs_for_ids(
    session: AsyncSession, status_ids: list[int]
) -> dict[int, CaseStatusRef]:
    """Batch id→ref for read projections (avoids N+1)."""
    ids = list({sid for sid in status_ids if sid is not None})
    if not ids:
        return {}
    rows = (
        await session.execute(select(CaseStatus).where(CaseStatus.id.in_(ids)))
    ).scalars()
    return {row.id: _ref(row) for row in rows}


async def ref_for_id(session: AsyncSession, status_id: int) -> CaseStatusRef | None:
    row = await session.get(CaseStatus, status_id)
    return _ref(row) if row is not None else None


async def label_for_id(session: AsyncSession, status_id: int) -> str:
    row = await session.get(CaseStatus, status_id)
    return row.label if row is not None else ""


async def in_use_count(session: AsyncSession, status_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Case).where(Case.status_id == status_id)
        )
    ) or 0


async def create_status(
    session: AsyncSession,
    status_in: CaseStatusCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> CaseStatus:
    position = status_in.position
    if position is None:
        max_pos = await session.scalar(
            select(func.max(CaseStatus.position)).where(
                CaseStatus.organisation_id == organisation_id
            )
        )
        position = (max_pos or 0) + 1
    row = CaseStatus(
        organisation_id=organisation_id,
        label=status_in.label,
        stage=status_in.stage,
        color=status_in.color,
        is_builtin=False,
        position=position,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def update_status(
    session: AsyncSession,
    row: CaseStatus,
    status_in: CaseStatusUpdate,
    updated_by: str,
) -> CaseStatus:
    update_data = status_in.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    row.sqlmodel_update(update_data)
    session.add(row)
    await session.flush()
    return row


async def delete_status(session: AsyncSession, row: CaseStatus) -> None:
    await session.delete(row)
    await session.flush()
