"""F1: MITRE ATT&CK pattern and procedure CRUD."""

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.pattern import Pattern, PatternImportItem, Procedure, ProcedureReplace


# --- Patterns ---

async def list_patterns(
    session: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Pattern], int]:
    base = select(Pattern).order_by(Pattern.name)
    return await paginate(session, base, Pattern.name, skip=skip, limit=limit)


async def get_pattern_by_external_id(
    session: AsyncSession, external_id: str
) -> Pattern | None:
    result = await session.execute(
        select(Pattern).where(Pattern.external_id == external_id)
    )
    return result.scalar_one_or_none()


async def import_patterns(
    session: AsyncSession,
    items: list[PatternImportItem],
    *,
    created_by: str,
) -> list[Pattern]:
    """Bulk upsert patterns by external_id (F1)."""
    out: list[Pattern] = []
    for item in items:
        existing = await get_pattern_by_external_id(session, item.external_id)
        if existing:
            existing.name = item.name
            existing.description = item.description
            existing.tactics = item.tactics
            existing.url = item.url
            existing.parent_external_id = item.parent_external_id
            existing.updated_by = created_by
            session.add(existing)
            out.append(existing)
        else:
            new = Pattern(
                external_id=item.external_id,
                name=item.name,
                description=item.description,
                tactics=item.tactics,
                url=item.url,
                parent_external_id=item.parent_external_id,
                created_by=created_by,
            )
            session.add(new)
            out.append(new)
    await session.flush()
    return out


async def case_stats(session: AsyncSession, organisation_id: str) -> dict[str, int]:
    """Distinct visible-case count per technique external_id for one org."""
    stmt = (
        select(Pattern.external_id, func.count(func.distinct(Procedure.case_id)))
        .join(Procedure, Procedure.pattern_id == Pattern.id)
        .join(Case, Case.id == Procedure.case_id)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
        )
        .group_by(Pattern.external_id)
    )
    rows = (await session.execute(stmt)).all()
    return {external_id: count for external_id, count in rows}


async def cases_for_pattern(
    session: AsyncSession, pattern_id: uuid.UUID, organisation_id: str
) -> list[Case]:
    """Org-visible, non-deleted cases linked to a pattern, newest first."""
    stmt = (
        select(Case)
        .join(Procedure, Procedure.case_id == Case.id)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            Procedure.pattern_id == pattern_id,
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
        )
        .order_by(Case.id.desc())
        .distinct()
    )
    return list((await session.execute(stmt)).scalars().all())


# --- Procedures ---

async def list_procedures(
    session: AsyncSession,
    case_id: int,
) -> list[Procedure]:
    result = await session.execute(
        select(Procedure).where(Procedure.case_id == case_id).order_by(Procedure.created_at)
    )
    return list(result.scalars().all())


async def list_alert_procedures(
    session: AsyncSession,
    alert_id: int,
) -> list[Procedure]:
    result = await session.execute(
        select(Procedure)
        .where(Procedure.alert_id == alert_id)
        .order_by(Procedure.created_at)
    )
    return list(result.scalars().all())


async def _resolve_pattern(
    session: AsyncSession, proc_item: PatternImportItem, created_by: str
) -> Pattern:
    """Return the catalog pattern for an import item, auto-importing it (by
    external_id) if the catalog doesn't know it yet."""
    pattern = await get_pattern_by_external_id(session, proc_item.external_id)
    if pattern is None:
        pattern = Pattern(
            external_id=proc_item.external_id,
            name=proc_item.name or proc_item.external_id,
            description=proc_item.description,
            tactics=proc_item.tactics,
            url=proc_item.url,
            parent_external_id=proc_item.parent_external_id,
            created_by=created_by,
        )
        session.add(pattern)
        await session.flush()
    return pattern


async def replace_procedures(
    session: AsyncSession,
    case_id: int,
    body: ProcedureReplace,
    *,
    created_by: str,
) -> list[Procedure]:
    """Replace all procedures for a case atomically."""
    await session.execute(
        sql_delete(Procedure).where(Procedure.case_id == case_id)
    )
    out: list[Procedure] = []
    for proc_item in body.procedures:
        pattern = await _resolve_pattern(session, proc_item, created_by)
        proc = Procedure(
            case_id=case_id,
            pattern_id=pattern.id,
            description=proc_item.description,
            created_by=created_by,
        )
        session.add(proc)
        out.append(proc)
    await session.flush()
    return out


async def replace_alert_procedures(
    session: AsyncSession,
    alert_id: int,
    body: ProcedureReplace,
    *,
    created_by: str,
) -> list[Procedure]:
    """Replace all procedures for an alert atomically (mirror of the case path)."""
    await session.execute(
        sql_delete(Procedure).where(Procedure.alert_id == alert_id)
    )
    out: list[Procedure] = []
    for proc_item in body.procedures:
        pattern = await _resolve_pattern(session, proc_item, created_by)
        proc = Procedure(
            alert_id=alert_id,
            pattern_id=pattern.id,
            description=proc_item.description,
            created_by=created_by,
        )
        session.add(proc)
        out.append(proc)
    await session.flush()
    return out


async def copy_alert_procedures_to_case(
    session: AsyncSession,
    *,
    alert_id: int,
    case_id: int,
    created_by: str,
) -> int:
    """Carry an alert's TTPs onto its case at promote/merge — additive and
    deduped by pattern (the case keeps any it already had). Returns the count of
    procedures newly copied."""
    alert_procs = await list_alert_procedures(session, alert_id)
    if not alert_procs:
        return 0
    existing = {p.pattern_id for p in await list_procedures(session, case_id)}
    copied = 0
    for proc in alert_procs:
        if proc.pattern_id in existing:
            continue
        session.add(
            Procedure(
                case_id=case_id,
                pattern_id=proc.pattern_id,
                description=proc.description,
                created_by=created_by,
            )
        )
        existing.add(proc.pattern_id)
        copied += 1
    await session.flush()
    return copied
