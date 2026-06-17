import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.core.db import get_session
from app.core.security import TokenPayload, decode_access_token
from app.crud.organisation_member import get_member_permissions
from app.crud.user import get_user_by_id
from app.models.case_ import Case, CaseStatus
from app.models.case_share import CaseShare
from app.models.role import Permission, RolePermission
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@dataclass
class AuthContext:
    user: User
    organisation_id: str
    permissions: set[str]


@dataclass
class CaseAuthContext:
    user: User
    organisation_id: str
    case: Case
    is_owner: bool
    permissions: set[str]


async def get_token_payload(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> TokenPayload:
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_user(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    user = await get_user_by_id(session, payload.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return user


async def get_superadmin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")
    return user


async def _resolve_org_permissions(
    session: AsyncSession,
    user: User,
    payload: TokenPayload,
    organisation_id: str,
) -> set[str]:
    """The permissions a user holds in an org. Superadmins get everything; everyone
    else must be a member (checked against the token's org claim)."""
    if user.is_superadmin:
        return {p.value for p in Permission}
    if organisation_id not in payload.organisations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organisation",
        )
    return await get_member_permissions(session, user.id, organisation_id)


async def get_org_context(
    organisation_id: Annotated[str, Path()],
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    permissions = await _resolve_org_permissions(session, user, payload, organisation_id)
    return AuthContext(user=user, organisation_id=organisation_id, permissions=permissions)


def require_permission(permission: str):
    async def _dep(ctx: Annotated[AuthContext, Depends(get_org_context)]) -> AuthContext:
        if permission not in ctx.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
        return ctx

    return Depends(_dep)


async def get_active_org_context(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_organisation_id: Annotated[str | None, Header(alias="X-Organisation-Id")] = None,
) -> AuthContext:
    """Resolve the active org from the X-Organisation-Id header. Used for case-scoped routes
    where the org isn't on the path."""
    if not x_organisation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Organisation-Id header is required",
        )
    permissions = await _resolve_org_permissions(session, user, payload, x_organisation_id)
    return AuthContext(user=user, organisation_id=x_organisation_id, permissions=permissions)


ActiveOrgContext = Annotated[AuthContext, Depends(get_active_org_context)]


async def _resolve_case_context(
    case_id: int,
    ctx: AuthContext,
    session: AsyncSession,
) -> CaseAuthContext:
    case = await session.get(Case, case_id)
    if not case or case.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if ctx.user.is_superadmin:
        return CaseAuthContext(
            user=ctx.user,
            organisation_id=ctx.organisation_id,
            case=case,
            is_owner=True,
            permissions=ctx.permissions,
        )

    share_result = await session.execute(
        select(CaseShare).where(
            CaseShare.case_id == case_id,
            CaseShare.organisation_id == ctx.organisation_id,
        )
    )
    share = share_result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    pinned = await session.execute(
        select(RolePermission.permission).where(RolePermission.role_id == share.role_id)
    )
    pinned_perms = set(pinned.scalars().all())
    effective = ctx.permissions & pinned_perms

    return CaseAuthContext(
        user=ctx.user,
        organisation_id=ctx.organisation_id,
        case=case,
        is_owner=share.is_owner,
        permissions=effective,
    )


def _assert_writable(case: Case, permission: str) -> None:
    """A merged-away (Duplicated) case is a frozen, read-only lineage tombstone.
    Block write:* operations on it (reads still pass). One chokepoint for every
    case-scoped write route. See docs/case-merge-design.md."""
    if permission.startswith("write:") and case.status == CaseStatus.duplicated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case is merged (duplicated) and read-only",
        )


def require_case_permission(permission: str):
    async def _dep(
        case_id: Annotated[int, Path()],
        ctx: ActiveOrgContext,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> CaseAuthContext:
        case_ctx = await _resolve_case_context(case_id, ctx, session)
        if permission not in case_ctx.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
        _assert_writable(case_ctx.case, permission)
        return case_ctx

    return Depends(_dep)


def require_case_owner(permission: str = "write:case"):
    """Permission AND owner-org check for owner-only operations
    (delete, share, TLP/PAP changes)."""

    async def _dep(
        case_id: Annotated[int, Path()],
        ctx: ActiveOrgContext,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> CaseAuthContext:
        case_ctx = await _resolve_case_context(case_id, ctx, session)
        if permission not in case_ctx.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
        if not case_ctx.is_owner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation restricted to the owner organisation",
            )
        _assert_writable(case_ctx.case, permission)
        return case_ctx

    return Depends(_dep)


@dataclass
class AnalyzerPrincipal:
    """The catlico-connector-engine service, authenticated by shared secret. v1 has one
    platform-level secret; this is the seam where a per-org API-key lookup slots in
    later (same Bearer header, richer principal)."""

    pass


async def get_analyzer_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> AnalyzerPrincipal:
    expected = settings.ANALYZER_SHARED_SECRET
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:]
    # Constant-time compare; reject when no secret is configured at all.
    if not expected or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid analyzer credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AnalyzerPrincipal()


CurrentUser = Annotated[User, Depends(get_current_user)]
SuperAdminUser = Annotated[User, Depends(get_superadmin_user)]
OrgContext = Annotated[AuthContext, Depends(get_org_context)]
Analyzer = Annotated[AnalyzerPrincipal, Depends(get_analyzer_principal)]
