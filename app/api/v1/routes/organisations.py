import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    OrgContext,
    SuperAdminUser,
    assert_permissions_grantable,
    get_granter_groups,
    require_permission,
)
from app.core.db import get_session
from app.crud import organisation as org_crud
from app.crud import organisation_link as link_crud
from app.crud import organisation_member as member_crud
from app.crud import role as role_crud
from app.crud import user as user_crud
from app.crud.audit import record_audit
from app.models.organisation import OrganisationCreate, OrganisationPublic, OrganisationUpdate
from app.models.organisation_link import (
    OrganisationLinkCreate,
    OrganisationLinkPublic,
    OrganisationLinkUpdate,
)
from app.models.organisation_member import (
    OrganisationMember,
    OrganisationMemberCreate,
    OrganisationMemberPublic,
    OrganisationMemberUpdate,
)
from app.models.user import User, UserCreate

router = APIRouter(prefix="/organisations", tags=["organisations"])


def _member_public(
    member: OrganisationMember, user: User | None
) -> OrganisationMemberPublic:
    return OrganisationMemberPublic.model_validate(
        {
            **member.model_dump(),
            "email": user.email if user else "",
            "first_name": user.first_name if user else None,
            "last_name": user.last_name if user else None,
            "has_avatar": user.has_avatar if user else False,
        }
    )


async def _member_user(
    session: AsyncSession, member: OrganisationMember
) -> User | None:
    return await user_crud.get_user_by_id(session, member.user_id)


@router.get("/", response_model=list[OrganisationPublic])
async def list_organisations(
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> list[OrganisationPublic]:
    return await org_crud.get_organisations(session, skip=skip, limit=limit)


@router.post("/", response_model=OrganisationPublic, status_code=status.HTTP_201_CREATED)
async def create_organisation(
    current_user: SuperAdminUser,
    org_in: OrganisationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationPublic:
    existing = await org_crud.get_organisation(session, org_in.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organisation '{org_in.id}' already exists",
        )
    org = await org_crud.create_organisation(session, org_in, created_by=str(current_user.id))
    await record_audit(
        session,
        action="create",
        obj=org,
        actor=str(current_user.id),
        details={"id": org.id, "name": org.name},
    )
    return org


@router.get("/{organisation_id}", response_model=OrganisationPublic)
async def get_organisation(
    ctx: Annotated[OrgContext, require_permission("read:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationPublic:
    org = await org_crud.get_organisation(session, ctx.organisation_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    return org


@router.patch("/{organisation_id}", response_model=OrganisationPublic)
async def update_organisation(
    org_in: OrganisationUpdate,
    ctx: Annotated[OrgContext, require_permission("write:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationPublic:
    org = await org_crud.get_organisation(session, ctx.organisation_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    org = await org_crud.update_organisation(
        session, org, org_in, updated_by=str(ctx.user.id)
    )
    await record_audit(
        session,
        action="update",
        obj=org,
        actor=str(ctx.user.id),
        details=org_in.model_dump(exclude_unset=True),
    )
    return org


@router.delete("/{organisation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organisation(
    current_user: SuperAdminUser,
    organisation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    org = await org_crud.get_organisation(session, organisation_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    await record_audit(session, action="delete", obj=org, actor=str(current_user.id))
    await org_crud.delete_organisation(session, org)


# --- Members ---

@router.get("/{organisation_id}/members", response_model=list[OrganisationMemberPublic])
async def list_members(
    ctx: Annotated[OrgContext, require_permission("read:user")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OrganisationMemberPublic]:
    rows = await member_crud.get_members(session, ctx.organisation_id)
    return [_member_public(member, user) for member, user in rows]


@router.post(
    "/{organisation_id}/members",
    response_model=OrganisationMemberPublic,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    member_in: OrganisationMemberCreate,
    ctx: Annotated[OrgContext, require_permission("write:user")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationMemberPublic:
    user_id = member_in.user_id
    if user_id is None:
        if member_in.email is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Either user_id or email is required",
            )
        user = await user_crud.get_user_by_email(session, str(member_in.email))
        if user is None:
            if not member_in.first_name or not member_in.last_name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="first_name and last_name are required when inviting a new user",
                )
            user = await user_crud.create_user(
                session,
                UserCreate(
                    email=member_in.email,
                    first_name=member_in.first_name,
                    last_name=member_in.last_name,
                ),
            )
            await record_audit(
                session,
                action="create",
                obj=user,
                context_type="organisation",
                context_id=ctx.organisation_id,
                actor=str(ctx.user.id),
                details={"email": user.email, "is_superadmin": user.is_superadmin},
                main_action=False,
            )
        user_id = user.id

    existing = await member_crud.get_member(session, user_id, ctx.organisation_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organisation",
        )
    role = await role_crud.get_role(session, member_in.role_id)
    if not role or role.organisation_id != ctx.organisation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    assert_permissions_grantable(
        set(await role_crud.get_role_permissions(session, role.id)),
        await get_granter_groups(session, ctx),
    )
    member = await member_crud.add_member(
        session,
        ctx.organisation_id,
        OrganisationMemberCreate(user_id=user_id, role_id=member_in.role_id),
        created_by=str(ctx.user.id),
    )
    await record_audit(
        session,
        action="create",
        obj=member,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
        details={"user_id": str(member.user_id), "role_id": str(member.role_id)},
    )
    return _member_public(member, await _member_user(session, member))


@router.patch(
    "/{organisation_id}/members/{member_user_id}",
    response_model=OrganisationMemberPublic,
)
async def update_member(
    member_user_id: uuid.UUID,
    member_in: OrganisationMemberUpdate,
    ctx: Annotated[OrgContext, require_permission("write:user")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationMemberPublic:
    member = await member_crud.get_member(session, member_user_id, ctx.organisation_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    role = await role_crud.get_role(session, member_in.role_id)
    if not role or role.organisation_id != ctx.organisation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    assert_permissions_grantable(
        set(await role_crud.get_role_permissions(session, role.id)),
        await get_granter_groups(session, ctx),
    )
    member = await member_crud.update_member(
        session, member, member_in, updated_by=str(ctx.user.id)
    )
    await record_audit(
        session,
        action="update",
        obj=member,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
        details={"role_id": str(member.role_id)},
    )
    return _member_public(member, await _member_user(session, member))


@router.delete(
    "/{organisation_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    member_user_id: uuid.UUID,
    ctx: Annotated[OrgContext, require_permission("write:user")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    member = await member_crud.get_member(session, member_user_id, ctx.organisation_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    await record_audit(
        session,
        action="delete",
        obj=member,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
    )
    await member_crud.remove_member(session, member)


# --- Links ---


@router.get(
    "/{organisation_id}/links", response_model=list[OrganisationLinkPublic]
)
async def list_links(
    organisation_id: str,
    _: Annotated[OrgContext, require_permission("read:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OrganisationLinkPublic]:
    return await link_crud.list_links(session, organisation_id)


@router.post(
    "/{organisation_id}/links",
    response_model=OrganisationLinkPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_link(
    organisation_id: str,
    link_in: OrganisationLinkCreate,
    ctx: Annotated[OrgContext, require_permission("write:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationLinkPublic:
    existing = await link_crud.get_link(session, organisation_id, link_in.to_org_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Link to '{link_in.to_org_id}' already exists",
        )
    return await link_crud.create_link(
        session, organisation_id, link_in, created_by=str(ctx.user.id)
    )


@router.patch(
    "/{organisation_id}/links/{to_org_id}",
    response_model=OrganisationLinkPublic,
)
async def update_link(
    organisation_id: str,
    to_org_id: str,
    link_in: OrganisationLinkUpdate,
    ctx: Annotated[OrgContext, require_permission("write:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrganisationLinkPublic:
    link = await link_crud.get_link(session, organisation_id, to_org_id)
    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Link not found"
        )
    return await link_crud.update_link(
        session, link, link_in, updated_by=str(ctx.user.id)
    )


@router.delete(
    "/{organisation_id}/links/{to_org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_link(
    organisation_id: str,
    to_org_id: str,
    ctx: Annotated[OrgContext, require_permission("write:organisation")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    link = await link_crud.get_link(session, organisation_id, to_org_id)
    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Link not found"
        )
    await link_crud.delete_link(session, link)
