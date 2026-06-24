from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SuperAdminUser
from app.core.db import get_session
from app.crud import observable as obs_crud
from app.models.observable import ObservableTypeCreate, ObservableTypePublic

router = APIRouter(prefix="/observable-types", tags=["observable-types"])


@router.get("/", response_model=list[ObservableTypePublic])
async def list_types(
    _: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ObservableTypePublic]:
    return await obs_crud.list_types(session)


@router.post(
    "/", response_model=ObservableTypePublic, status_code=status.HTTP_201_CREATED
)
async def create_type(
    admin: SuperAdminUser,
    type_in: ObservableTypeCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservableTypePublic:
    existing = await obs_crud.get_type(session, type_in.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Observable type '{type_in.name}' already exists",
        )
    return await obs_crud.create_type(
        session, type_in, created_by=str(admin.id)
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_type(
    admin: SuperAdminUser,
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    type_ = await obs_crud.get_type(session, name)
    if type_ is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Observable type '{name}' not found",
        )
    await obs_crud.delete_type(session, type_, deleted_by=str(admin.id))
