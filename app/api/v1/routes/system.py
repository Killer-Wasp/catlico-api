from fastapi import APIRouter

from app.core.extensions import registry

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/capabilities")
async def system_capabilities() -> dict[str, bool]:
    """The merged platform capability flags (e.g. ``{"sso": false, "mfa": false}``
    in OSS). Unauthenticated: the web reads it before login to decide whether to
    render enterprise UI. An installed extension flips the relevant flags on."""
    return registry.capabilities()
