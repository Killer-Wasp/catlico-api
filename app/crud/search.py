"""Global search: query classification and per-entity federated queries.

One query per entity type, each reusing that type's existing visibility
predicate — search must never return a row the corresponding list view would
hide. Spec: docs/global-search-design.md (catlico workspace root).
"""

import ipaddress
import uuid
from dataclasses import dataclass

from sqlalchemy import String, Text, and_, cast, func, literal_column, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import user as user_crud
from app.crud.task import _visible_task_condition
from app.models.alert import Alert
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.comment import Comment, CommentEntityType, _display_name_from_email
from app.models.search import AlertHit, CaseHit, CommentHit, TaskHit
from app.models.task import Task
from app.util.ids import format_task_id


@dataclass(frozen=True)
class ClassifiedQuery:
    """How the raw query string should be matched.

    net is a normalized address/CIDR string when the query parses as one
    (drives inet matching on observables); other entity types always get the
    text treatment regardless.
    """

    text: str
    net: str | None
    is_bare_ip: bool


def classify_query(q: str) -> ClassifiedQuery:
    raw = q.strip()
    try:
        return ClassifiedQuery(text=raw, net=str(ipaddress.ip_address(raw)), is_bare_ip=True)
    except ValueError:
        pass
    if "/" in raw:
        try:
            return ClassifiedQuery(
                text=raw, net=str(ipaddress.ip_network(raw, strict=False)), is_bare_ip=False
            )
        except ValueError:
            pass
    return ClassifiedQuery(text=raw, net=None, is_bare_ip=False)


def prefix_tsquery(q: str):
    """tsquery from user input, prefix-matching the last token so typing feels
    live. websearch_to_tsquery never raises on any input (the query string is
    data, not syntax); its text form always ends in a quoted token, so
    appending ':*' is valid. Empty tsquery -> NULL -> matches nothing.
    """
    base_txt = cast(func.websearch_to_tsquery("simple", q), Text)
    return func.to_tsquery("simple", func.nullif(base_txt, "") + ":*")


def like_pattern(q: str) -> str:
    """%q% with LIKE wildcards escaped; use with .ilike(pattern, escape="\\\\")."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


#: Generated columns (Task 1 migration) aren't mapped on the SQLModel classes —
#: reference them by qualified name.
_CASE_TSV = literal_column("case_.search_tsv")
_ALERT_TSV = literal_column("alert.search_tsv")
_TASK_TSV = literal_column("task.search_tsv")

_HEADLINE_OPTS = "StartSel=<mark>, StopSel=</mark>, MaxWords=18, MinWords=6"


def _headline(source, tsq):
    return func.ts_headline("simple", source, tsq, _HEADLINE_OPTS)


async def search_cases(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[CaseHit], int]:
    tsq = prefix_tsquery(q)
    base = (
        select(Case)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
            _CASE_TSV.op("@@")(tsq),
        )
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.add_columns(
                _headline(
                    Case.title + " — " + func.coalesce(Case.description, ""), tsq
                )
            )
            .order_by(
                func.ts_rank(_CASE_TSV, tsq).desc(),
                func.coalesce(Case.updated_at, Case.created_at).desc(),
                Case.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).all()
    hits = [
        CaseHit(
            id=case.id,
            title=case.title,
            snippet=snippet,
            status=case.status,
            severity=case.severity,
            updated_at=case.updated_at,
            created_at=case.created_at,
        )
        for case, snippet in rows
    ]
    return hits, total


async def search_alerts(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[AlertHit], int]:
    tsq = prefix_tsquery(q)
    base = select(Alert).where(
        Alert.organisation_id == organisation_id,
        Alert.deleted_at.is_(None),
        _ALERT_TSV.op("@@")(tsq),
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.add_columns(
                _headline(
                    Alert.title + " — " + func.coalesce(Alert.description, ""), tsq
                )
            )
            .order_by(
                func.ts_rank(_ALERT_TSV, tsq).desc(),
                func.coalesce(Alert.updated_at, Alert.created_at).desc(),
                Alert.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).all()
    hits = [
        AlertHit(
            id=alert.id,
            title=alert.title,
            snippet=snippet,
            status=alert.status,
            severity=alert.severity,
        )
        for alert, snippet in rows
    ]
    return hits, total


async def search_tasks(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[TaskHit], int]:
    tsq = prefix_tsquery(q)
    base = select(Task).where(
        _visible_task_condition(organisation_id),
        Task.deleted_at.is_(None),
        _TASK_TSV.op("@@")(tsq),
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(
                func.ts_rank(_TASK_TSV, tsq).desc(),
                func.coalesce(Task.updated_at, Task.created_at).desc(),
                Task.case_id.desc(),
                Task.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).scalars()
    hits = [
        TaskHit(
            case_id=t.case_id,
            id=t.id,
            public_id=format_task_id(t.case_id, t.id),
            title=t.title,
            status=t.status,
        )
        for t in rows
    ]
    return hits, total


_COMMENT_TSV = literal_column("comment.search_tsv")


async def search_comments(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[CommentHit], int]:
    tsq = prefix_tsquery(q)
    visible_case_ids = select(cast(CaseShare.case_id, String)).where(
        CaseShare.organisation_id == organisation_id
    )
    owned_alert_ids = select(cast(Alert.id, String)).where(
        Alert.organisation_id == organisation_id,
        Alert.deleted_at.is_(None),
    )
    base = select(Comment).where(
        Comment.deleted_at.is_(None),
        _COMMENT_TSV.op("@@")(tsq),
        or_(
            and_(
                Comment.entity_type == CommentEntityType.case,
                Comment.entity_id.in_(visible_case_ids),
            ),
            and_(
                Comment.entity_type == CommentEntityType.alert,
                Comment.entity_id.in_(owned_alert_ids),
            ),
        ),
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.add_columns(_headline(Comment.message, tsq))
            .order_by(
                func.ts_rank(_COMMENT_TSV, tsq).desc(),
                Comment.created_at.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).all()

    author_ids = []
    for comment, _ in rows:
        try:
            author_ids.append(uuid.UUID(comment.created_by))
        except ValueError:
            pass
    emails = await user_crud.emails_for_ids(session, author_ids) if author_ids else {}

    def _author(created_by: str) -> str:
        try:
            return _display_name_from_email(emails.get(uuid.UUID(created_by), ""))
        except ValueError:
            return ""

    hits = [
        CommentHit(
            id=comment.id,
            entity_type=comment.entity_type,
            entity_id=comment.entity_id,
            snippet=snippet,
            author_name=_author(comment.created_by),
            created_at=comment.created_at,
        )
        for comment, snippet in rows
    ]
    return hits, total
