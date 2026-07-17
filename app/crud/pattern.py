"""F1: MITRE ATT&CK pattern and procedure CRUD."""

from sqlalchemy import Integer, String, and_, cast, func, or_
from sqlalchemy import delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.pattern import Pattern, PatternImportItem, Procedure, ProcedureReplace
from app.models.tag import Tag, TaggableType, Tagging


def _case_tagged_with_technique(external_id: str):
    """EXISTS clause: the current-row Case carries a bare technique-id tag
    (e.g. `T1189`, `T1566.002`) matching `external_id`. Analysts commonly tag a
    case with the technique id instead of (or as well as) creating a formal TTP
    procedure, so the matrix treats either as "observed this technique"."""
    return (
        select(Tagging.tag_id)
        .join(Tag, Tag.id == Tagging.tag_id)
        .where(
            Tagging.taggable_type == TaggableType.case,
            Tagging.taggable_id == cast(Case.id, String),
            Tag.namespace == "",
            Tag.value == "",
            Tag.predicate == external_id,
        )
        .exists()
    )


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
    """Distinct visible-case count per technique external_id for one org.

    A case counts for a technique if it either links the pattern via a Procedure
    (formal TTP) or carries the technique id as a plain tag — see
    `_case_tagged_with_technique`. The two sources are UNION-ed as
    (external_id, case_id) pairs so a case linked both ways is counted once."""
    visible = (
        select(Case.id)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
        )
        .subquery()
    )
    by_procedure = (
        select(
            Pattern.external_id.label("external_id"),
            Procedure.case_id.label("case_id"),
        )
        .join(Procedure, Procedure.pattern_id == Pattern.id)
        .where(Procedure.case_id.in_(select(visible.c.id)))
    )
    by_tag = (
        select(
            Pattern.external_id.label("external_id"),
            cast(Tagging.taggable_id, Integer).label("case_id"),
        )
        .join(
            Tag,
            and_(
                Tag.predicate == Pattern.external_id,
                Tag.namespace == "",
                Tag.value == "",
            ),
        )
        .join(
            Tagging,
            and_(
                Tagging.tag_id == Tag.id,
                Tagging.taggable_type == TaggableType.case,
            ),
        )
        .where(cast(Tagging.taggable_id, Integer).in_(select(visible.c.id)))
    )
    pairs = by_procedure.union(by_tag).subquery()
    stmt = select(
        pairs.c.external_id, func.count(func.distinct(pairs.c.case_id))
    ).group_by(pairs.c.external_id)
    rows = (await session.execute(stmt)).all()
    return {external_id: count for external_id, count in rows}


async def cases_for_pattern(
    session: AsyncSession, pattern: Pattern, organisation_id: str
) -> list[Case]:
    """Org-visible, non-deleted cases that observed a technique, newest first —
    linked either by a Procedure (formal TTP) or by a bare technique-id tag."""
    linked_by_procedure = (
        select(Procedure.id)
        .where(
            Procedure.case_id == Case.id,
            Procedure.pattern_id == pattern.id,
        )
        .exists()
    )
    stmt = (
        select(Case)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
            or_(
                linked_by_procedure,
                _case_tagged_with_technique(pattern.external_id),
            ),
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
