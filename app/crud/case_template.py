from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.crud.pagination import paginate
from app.crud.task import allocate_task_public_ids
from app.models.case_template import (
    CaseTemplate,
    CaseTemplateCreate,
    CaseTemplateTask,
    CaseTemplateUpdate,
)
from app.models.task import Task


async def get_template(
    session: AsyncSession, template_id: int, organisation_id: str
) -> CaseTemplate | None:
    tpl = await session.get(CaseTemplate, template_id)
    if tpl is None or tpl.deleted_at is not None:
        return None
    if tpl.organisation_id != organisation_id:
        return None
    return tpl


async def get_template_by_name(
    session: AsyncSession, name: str, organisation_id: str
) -> CaseTemplate | None:
    result = await session.execute(
        select(CaseTemplate).where(
            CaseTemplate.name == name,
            CaseTemplate.organisation_id == organisation_id,
            CaseTemplate.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_templates(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[CaseTemplate], int]:
    base = select(CaseTemplate).where(
        CaseTemplate.organisation_id == organisation_id,
        CaseTemplate.deleted_at.is_(None),
    )
    return await paginate(session, base, CaseTemplate.name, skip=skip, limit=limit)


async def list_template_tasks(
    session: AsyncSession, template_id: int
) -> list[CaseTemplateTask]:
    result = await session.execute(
        select(CaseTemplateTask)
        .where(CaseTemplateTask.template_id == template_id)
        .order_by(CaseTemplateTask.order, CaseTemplateTask.title)
    )
    return list(result.scalars().all())


async def create_template(
    session: AsyncSession,
    tpl_in: CaseTemplateCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> CaseTemplate:
    tpl = CaseTemplate(
        name=tpl_in.name,
        display_name=tpl_in.display_name,
        title_prefix=tpl_in.title_prefix,
        description=tpl_in.description,
        severity=tpl_in.severity,
        tlp=tpl_in.tlp,
        pap=tpl_in.pap,
        summary=tpl_in.summary,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(tpl)
    await session.flush()
    for t in tpl_in.tasks:
        session.add(
            CaseTemplateTask(
                template_id=tpl.id,
                title=t.title,
                group=t.group,
                description=t.description,
                order=t.order,
            )
        )
    await session.flush()
    return tpl


async def update_template(
    session: AsyncSession,
    tpl: CaseTemplate,
    tpl_in: CaseTemplateUpdate,
    updated_by: str,
) -> CaseTemplate:
    update_data = tpl_in.model_dump(exclude_unset=True)
    tasks = update_data.pop("tasks", None)
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    tpl.sqlmodel_update(update_data)
    session.add(tpl)
    if tasks is not None:
        # Replace the task scaffolding wholesale.
        await session.execute(
            delete(CaseTemplateTask).where(CaseTemplateTask.template_id == tpl.id)
        )
        for t in tasks:
            session.add(
                CaseTemplateTask(
                    template_id=tpl.id,
                    title=t["title"],
                    group=t.get("group", ""),
                    description=t.get("description", ""),
                    order=t.get("order", 0),
                )
            )
    await session.flush()
    return tpl


async def delete_template(
    session: AsyncSession, tpl: CaseTemplate, deleted_by: str
) -> None:
    tpl.deleted_at = datetime.now(UTC)
    tpl.deleted_by = deleted_by
    session.add(tpl)
    await session.flush()


async def scaffold_tasks_into_case(
    session: AsyncSession,
    *,
    template_id: int,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> int:
    """Create the template's tasks on the new case (in-transaction). Returns the count."""
    tasks = await list_template_tasks(session, template_id)
    public_ids = await allocate_task_public_ids(session, case_id, count=len(tasks))
    for public_id, t in zip(public_ids, tasks, strict=True):
        session.add(
            Task(
                public_id=public_id,
                case_id=case_id,
                organisation_id=organisation_id,
                title=t.title,
                group=t.group,
                description=t.description,
                order=t.order,
                created_by=created_by,
            )
        )
    await session.flush()
    return len(tasks)
