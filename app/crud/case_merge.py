import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._seq import next_attachment_ids, next_task_ids
from app.crud.audit import record_audit
from app.models.alert import Alert
from app.models.attachment import AttachmentLink
from app.models.case_ import Case, CaseCreate, CaseResolutionStatus, CaseStatus
from app.models.case_merge import CaseMerge
from app.models.case_share import CaseShare
from app.models.comment import Comment, CommentEntityType
from app.models.flag import Flag, FlagEntityType
from app.models.log import Log
from app.models.observable import Observable, ObservableShare
from app.models.tag import Tagging, TaggableType
from app.models.task import Task
from app.models.task_share import TaskShare


class MergeError(Exception):
    """Raised by merge_cases on a validation failure. Carries an HTTP status so the
    route layer can translate it without re-deriving intent."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def _reparent_observables(
    session: AsyncSession, source_ids: list[int], new_case_id: int, actor: str
) -> int:
    """Move source observables onto the new case, deduping on (type, data)."""
    obs_list = (
        (
            await session.execute(
                select(Observable)
                .where(
                    Observable.case_id.in_(source_ids),
                    Observable.deleted_at.is_(None),
                )
                .order_by(Observable.created_at, Observable.id)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    survivors: dict[tuple[str, str], Observable] = {}
    moved = 0
    for obs in obs_list:
        key = (obs.observable_type, obs.data)
        survivor = survivors.get(key)
        if survivor is None:
            obs.case_id = new_case_id
            session.add(obs)
            survivors[key] = obs
            moved += 1
            continue
        survivor.ioc = survivor.ioc or obs.ioc
        survivor.sighted = survivor.sighted or obs.sighted
        survivor.ignore_similarity = survivor.ignore_similarity and obs.ignore_similarity
        survivor.tlp = max(survivor.tlp, obs.tlp)
        if obs.message and obs.message not in (survivor.message or ""):
            survivor.message = (
                f"{survivor.message}\n{obs.message}".strip()
                if survivor.message
                else obs.message
            )
        session.add(survivor)
        await _union_observable_shares(session, obs.id, survivor.id)
        obs.deleted_at = now
        obs.deleted_by = actor
        session.add(obs)
    return moved


async def _union_observable_shares(
    session: AsyncSession, twin_id: uuid.UUID, survivor_id: uuid.UUID
) -> None:
    twin_shares = (
        (
            await session.execute(
                select(ObservableShare).where(ObservableShare.observable_id == twin_id)
            )
        )
        .scalars()
        .all()
    )
    if not twin_shares:
        return
    existing = set(
        (
            await session.execute(
                select(ObservableShare.organisation_id).where(
                    ObservableShare.observable_id == survivor_id
                )
            )
        )
        .scalars()
        .all()
    )
    for share in twin_shares:
        if share.organisation_id not in existing:
            session.add(
                ObservableShare(
                    observable_id=survivor_id,
                    organisation_id=share.organisation_id,
                    created_by=share.created_by,
                )
            )
            existing.add(share.organisation_id)
        await session.delete(share)


async def _reparent_tasks(
    session: AsyncSession, source_ids: list[int], new_case_id: int
) -> tuple[dict[tuple[int, int], int], int]:
    rows = (
        await session.execute(
            select(Task.case_id, Task.id)
            .where(Task.case_id.in_(source_ids), Task.deleted_at.is_(None))
            .order_by(Task.case_id, Task.created_at, Task.id)
        )
    ).all()
    if not rows:
        return {}, 0
    new_ids = await next_task_ids(session, new_case_id, count=len(rows))
    mapping: dict[tuple[int, int], int] = {}
    for (old_case, old_id), new_id in zip(rows, new_ids, strict=True):
        mapping[(old_case, old_id)] = new_id
        opts = {"synchronize_session": False}
        await session.execute(
            update(Log)
            .where(Log.case_id == old_case, Log.task_id == old_id)
            .values(case_id=new_case_id, task_id=new_id)
            .execution_options(**opts)
        )
        await session.execute(
            update(TaskShare)
            .where(TaskShare.case_id == old_case, TaskShare.task_id == old_id)
            .values(case_id=new_case_id, task_id=new_id)
            .execution_options(**opts)
        )
        await session.execute(
            update(Task)
            .where(Task.case_id == old_case, Task.id == old_id)
            .values(case_id=new_case_id, id=new_id)
            .execution_options(**opts)
        )
    return mapping, len(rows)


async def _reparent_case_attachments(
    session: AsyncSession,
    source_ids: list[int],
    new_case_id: int,
    task_mapping: dict[tuple[int, int], int],
) -> int:
    rows = (
        await session.execute(
            select(AttachmentLink.case_id, AttachmentLink.id, AttachmentLink.owner_task_id)
            .where(
                AttachmentLink.case_id.in_(source_ids),
                AttachmentLink.deleted_at.is_(None),
            )
            .order_by(AttachmentLink.case_id, AttachmentLink.id)
        )
    ).all()
    if not rows:
        return 0
    new_ids = await next_attachment_ids(session, new_case_id, count=len(rows))
    for (old_case, old_id, owner_task_id), new_id in zip(rows, new_ids, strict=True):
        new_owner_task = (
            task_mapping.get((old_case, owner_task_id))
            if owner_task_id is not None
            else None
        )
        await session.execute(
            update(AttachmentLink)
            .where(AttachmentLink.case_id == old_case, AttachmentLink.id == old_id)
            .values(case_id=new_case_id, id=new_id, owner_task_id=new_owner_task)
            .execution_options(synchronize_session=False)
        )
    return len(rows)


async def merge_cases(
    session: AsyncSession,
    *,
    source_ids: list[int],
    case_in: CaseCreate,
    owner_org_id: str,
    owner_role_id: uuid.UUID,
    actor: str,
) -> Case:
    """Merge 2+ same-owner-org cases into a fresh case (create-new model)."""
    distinct_ids = sorted(set(source_ids))
    if len(distinct_ids) < 2:
        raise MergeError(400, "Merge requires at least 2 distinct cases")

    sources = (
        (
            await session.execute(
                select(Case)
                .where(Case.id.in_(distinct_ids))
                .order_by(Case.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in sources}
    for cid in distinct_ids:
        case = by_id.get(cid)
        if case is None or case.deleted_at is not None:
            raise MergeError(404, f"Case {cid} not found")
        if case.status == CaseStatus.duplicated:
            raise MergeError(409, f"Case {cid} is already merged")

    owner_rows = (
        await session.execute(
            select(CaseShare.case_id, CaseShare.organisation_id).where(
                CaseShare.case_id.in_(distinct_ids),
                CaseShare.is_owner.is_(True),
            )
        )
    ).all()
    owner_by_case = {cid: org for cid, org in owner_rows}
    for cid in distinct_ids:
        if owner_by_case.get(cid) != owner_org_id:
            raise MergeError(
                403, "All source cases must be owned by your organisation"
            )

    max_tlp = max(c.tlp for c in sources)
    max_pap = max(c.pap for c in sources)
    if case_in.tlp < max_tlp or case_in.pap < max_pap:
        raise MergeError(
            422, "Merged case TLP/PAP cannot be less restrictive than its sources"
        )

    new_case = Case(
        title=case_in.title,
        description=case_in.description,
        severity=case_in.severity,
        tlp=case_in.tlp,
        pap=case_in.pap,
        assignee_id=case_in.assignee_id,
        start_date=case_in.start_date,
        summary=case_in.summary,
        created_by=actor,
    )
    session.add(new_case)
    await session.flush()
    session.add(
        CaseShare(
            case_id=new_case.id,
            organisation_id=owner_org_id,
            role_id=owner_role_id,
            is_owner=True,
            created_by=actor,
        )
    )
    await session.flush()

    source_id_strs = [str(cid) for cid in distinct_ids]
    task_mapping, moved_tasks = await _reparent_tasks(
        session, distinct_ids, new_case.id
    )
    moved_attachments = await _reparent_case_attachments(
        session, distinct_ids, new_case.id, task_mapping
    )
    moved_obs = await _reparent_observables(
        session, distinct_ids, new_case.id, actor
    )
    comment_res = await session.execute(
        update(Comment)
        .where(
            Comment.entity_type == CommentEntityType.case,
            Comment.entity_id.in_(source_id_strs),
            Comment.deleted_at.is_(None),
        )
        .values(entity_id=str(new_case.id))
    )
    alert_res = await session.execute(
        update(Alert)
        .where(Alert.case_id.in_(distinct_ids), Alert.deleted_at.is_(None))
        .values(case_id=new_case.id)
    )
    tag_ids = list(
        (
            await session.execute(
                select(Tagging.tag_id)
                .where(
                    Tagging.taggable_type == TaggableType.case,
                    Tagging.taggable_id.in_(source_id_strs),
                )
                .distinct()
            )
        ).scalars()
    )
    await session.execute(
        delete(Tagging).where(
            Tagging.taggable_type == TaggableType.case,
            Tagging.taggable_id.in_(source_id_strs),
        )
    )
    for tag_id in tag_ids:
        session.add(
            Tagging(
                tag_id=tag_id,
                taggable_type=TaggableType.case,
                taggable_id=str(new_case.id),
            )
        )
    flag_orgs = list(
        (
            await session.execute(
                select(Flag.organisation_id)
                .where(
                    Flag.entity_type == FlagEntityType.case,
                    Flag.entity_id.in_(source_id_strs),
                )
                .distinct()
            )
        ).scalars()
    )
    await session.execute(
        delete(Flag).where(
            Flag.entity_type == FlagEntityType.case,
            Flag.entity_id.in_(source_id_strs),
        )
    )
    for org in flag_orgs:
        session.add(
            Flag(
                entity_type=FlagEntityType.case,
                entity_id=str(new_case.id),
                organisation_id=org,
                created_by=actor,
            )
        )

    secondary_orgs = sorted(
        {
            org
            for _cid, org in (
                await session.execute(
                    select(CaseShare.case_id, CaseShare.organisation_id).where(
                        CaseShare.case_id.in_(distinct_ids),
                        CaseShare.is_owner.is_(False),
                    )
                )
            ).all()
        }
    )

    now = datetime.now(UTC)
    for case in sources:
        case.status = CaseStatus.duplicated
        case.resolution_status = CaseResolutionStatus.duplicated
        case.end_date = now
        case.updated_at = now
        case.updated_by = actor
        session.add(case)
        session.add(
            CaseMerge(
                source_case_id=case.id,
                target_case_id=new_case.id,
                created_by=actor,
            )
        )
    await session.flush()

    moved = {
        "tasks": moved_tasks,
        "attachments": moved_attachments,
        "observables": moved_obs,
        "comments": comment_res.rowcount or 0,
        "alerts": alert_res.rowcount or 0,
        "tags": len(tag_ids),
        "flags": len(flag_orgs),
    }
    await record_audit(
        session,
        action="merge",
        obj=new_case,
        context=new_case,
        actor=actor,
        details={
            "sources": distinct_ids,
            "moved": moved,
            "shares_not_carried": secondary_orgs,
        },
        organisation_id=owner_org_id,
    )
    for case in sources:
        await record_audit(
            session,
            action="merge",
            obj=case,
            context=case,
            actor=actor,
            main_action=False,
            details={"merged_into": new_case.id},
            organisation_id=owner_org_id,
        )
    return new_case
