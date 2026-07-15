"""Global search: query classification and per-entity federated queries.

One query per entity type, each reusing that type's existing visibility
predicate — search must never return a row the corresponding list view would
hide. Covers cases, alerts, observables, tasks, comments, knowledge-base pages,
and attachment filenames. Spec: docs/global-search-design.md (catlico workspace
root).
"""

import ipaddress
import uuid
from dataclasses import dataclass

from sqlalchemy import String, Text, and_, cast, func, literal_column, or_
from sqlalchemy import case as sa_case
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import user as user_crud
from app.crud.observable import _visible_observable_condition
from app.crud.task import _visible_task_condition
from app.models.alert import Alert
from app.models.attachment import AttachmentLink
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.comment import Comment, CommentEntityType, _display_name_from_email
from app.models.knowledge_base import KnowledgeBasePage
from app.models.observable import Observable
from app.models.search import (
    AlertHit,
    AttachmentHit,
    CaseHit,
    CommentHit,
    KnowledgeBaseHit,
    ObservableGroupHit,
    ObservableHit,
    TaskHit,
)
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


#: Control characters Postgres text can't carry (NUL) or that only ever arrive
#: by accident (the rest of C0, minus tab/newline which strip() handles).
_CONTROL_CHARS = dict.fromkeys(range(32))


def sanitize_query(q: str) -> str:
    """Strip control characters, then whitespace. A NUL byte reaches asyncpg as
    an invalid UTF-8 sequence and raises, so it must never survive to a bind
    parameter — the query string is data, and no input may produce a 500."""
    return q.translate(_CONTROL_CHARS).strip()


def classify_query(q: str) -> ClassifiedQuery:
    raw = sanitize_query(q)
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
                    # Same fields the tsvector indexes, so a match found only
                    # in summary still yields a highlighted snippet.
                    Case.title
                    + " — "
                    + func.coalesce(Case.description, "")
                    + " "
                    + func.coalesce(Case.summary, ""),
                    tsq,
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
    from app.crud import case_status as case_status_crud

    status_refs = await case_status_crud.refs_for_ids(
        session, [case.status_id for case, _ in rows]
    )
    hits = [
        CaseHit(
            id=case.id,
            title=case.title,
            snippet=snippet,
            status=status_refs.get(case.status_id),
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
                    # Same fields the tsvector indexes, so a match found only
                    # in source_ref still yields a highlighted snippet.
                    Alert.title
                    + " — "
                    + func.coalesce(Alert.description, "")
                    + " "
                    + func.coalesce(Alert.source_ref, ""),
                    tsq,
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
                Comment.id.desc(),
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


_OBSERVABLE_TSV = literal_column("observable.search_tsv")


def _observable_match_and_rank(cq: ClassifiedQuery):
    """(match condition, rank ordering) for the observable bucket, by query
    class. inet matches rank above trigram matches; text mode ranks by
    trigram similarity."""
    pattern = like_pattern(cq.text)
    text_cond = or_(
        Observable.data.ilike(pattern, escape="\\"),
        _OBSERVABLE_TSV.op("@@")(prefix_tsquery(cq.text)),
    )
    if cq.net is None:
        return text_cond, [func.similarity(Observable.data, cq.text).desc()]
    net = cast(cq.net, INET)
    if cq.is_bare_ip:
        # exact, inside a stored range, or textual (IP embedded in a URL etc.)
        inet_cond = or_(Observable.ip == net, net.op("<<=")(Observable.ip))
        cond = or_(inet_cond, text_cond)
        return cond, [sa_case((inet_cond, 0), else_=1).asc()]
    # CIDR query: stored addresses and sub-ranges inside it; no text fallback
    # for observables (the literal string still text-matches other buckets).
    return Observable.ip.op("<<=")(net), []


async def search_observables(
    session: AsyncSession,
    organisation_id: str,
    q: str,
    *,
    skip: int = 0,
    limit: int = 10,
    group: bool = False,
) -> tuple[list[ObservableHit], list[ObservableGroupHit], int]:
    cq = classify_query(q)
    cond, rank = _observable_match_and_rank(cq)
    where = [
        _visible_observable_condition(organisation_id),
        Observable.deleted_at.is_(None),
        cond,
    ]
    total = (
        await session.execute(
            select(func.count()).select_from(
                select(Observable.id).where(*where).subquery()
            )
        )
    ).scalar_one()

    if group:
        rows = (
            await session.execute(
                select(
                    Observable.observable_type,
                    Observable.data,
                    func.count().label("occurrences"),
                )
                .where(*where)
                .group_by(Observable.observable_type, Observable.data)
                .order_by(func.count().desc(), Observable.data.asc())
                .offset(skip)
                .limit(limit)
            )
        ).all()
        groups = [
            ObservableGroupHit(observable_type=t, data=d, occurrences=n)
            for t, d, n in rows
        ]
        return [], groups, total

    rows = (
        await session.execute(
            select(Observable)
            .where(*where)
            .order_by(
                *rank,
                func.coalesce(Observable.updated_at, Observable.created_at).desc(),
                Observable.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).scalars()
    hits = [
        ObservableHit(
            id=o.id,
            observable_type=o.observable_type,
            data=o.data,
            case_id=o.case_id,
            alert_id=o.alert_id,
            ioc=o.ioc,
            message=o.message,
            verdict=o.verdict,
        )
        for o in rows
    ]
    return hits, [], total


async def search_knowledge_base(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[KnowledgeBaseHit], int]:
    """KB pages the org owns, matched on title + summary + body. Pages have no
    stored tsvector (unlike cases/alerts), so the document is assembled and
    tokenised inline — same FTS semantics (prefix-while-typing, word matching)
    as the other text buckets, without a migration. Caller gates on
    read:knowledge_base before invoking."""
    tsq = prefix_tsquery(q)
    doc = (
        KnowledgeBasePage.title
        + " "
        + func.coalesce(KnowledgeBasePage.summary, "")
        + " "
        + func.coalesce(KnowledgeBasePage.content, "")
    )
    tsv = func.to_tsvector("simple", doc)
    base = select(KnowledgeBasePage).where(
        KnowledgeBasePage.organisation_id == organisation_id,
        KnowledgeBasePage.deleted_at.is_(None),
        tsv.op("@@")(tsq),
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.add_columns(_headline(doc, tsq))
            .order_by(
                func.ts_rank(tsv, tsq).desc(),
                func.coalesce(
                    KnowledgeBasePage.updated_at, KnowledgeBasePage.created_at
                ).desc(),
                KnowledgeBasePage.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).all()
    hits = [
        KnowledgeBaseHit(id=page.id, title=page.title, snippet=snippet)
        for page, snippet in rows
    ]
    return hits, total


async def search_attachments(
    session: AsyncSession, organisation_id: str, q: str, *, skip: int = 0, limit: int = 10
) -> tuple[list[AttachmentHit], int]:
    """Case-bound attachments whose filename matches, scoped to cases the org can
    see. Filenames aren't natural language, so match is a substring ILIKE (not
    FTS). Visibility reuses the case bucket's ANY-share predicate: if the org can
    see the parent case, it can see the file — so a soft-deleted case, or one the
    org holds no CaseShare on, drops out."""
    pattern = like_pattern(q)
    visible_case_ids = select(CaseShare.case_id).where(
        CaseShare.organisation_id == organisation_id
    )
    base = (
        select(AttachmentLink)
        .join(Case, Case.id == AttachmentLink.case_id)
        .where(
            AttachmentLink.deleted_at.is_(None),
            Case.deleted_at.is_(None),
            AttachmentLink.case_id.in_(visible_case_ids),
            AttachmentLink.name.ilike(pattern, escape="\\"),
        )
    )
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(
                AttachmentLink.created_at.desc(),
                AttachmentLink.case_id.desc(),
                AttachmentLink.id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
    ).scalars()
    hits = [
        AttachmentHit(
            id=link.id,
            public_id=link.public_id,
            case_id=link.case_id,
            attachment_id=link.attachment_id,
            name=link.name,
        )
        for link in rows
    ]
    return hits, total
