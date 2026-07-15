"""A minimal, test-only Catlico extension.

It exercises every hook of the extension seam (`app.core.extensions`) so a test
can prove the OSS-side seam works end-to-end without the real, private
`catlico-enterprise` package installed via entry points. This is exactly the
shape `catlico-enterprise` (task #32) must implement.
"""

from fastapi import APIRouter

from app.core.extensions import Challenge, IdentityProvider
from app.models.user import User

router = APIRouter(prefix="/fixture-ext", tags=["fixture-ext"])


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}


class FixtureExtension:
    """Implements the full (optional) `Extension` protocol."""

    def routers(self) -> list[APIRouter]:
        return [router]

    def identity_providers(self) -> list[IdentityProvider]:
        return [
            IdentityProvider(
                id="fixture-oidc",
                name="Fixture SSO",
                kind="oidc",
                authorize_path="/api/v1/fixture-ext/authorize",
            )
        ]

    def capabilities(self) -> dict[str, bool]:
        return {"sso": True, "mfa": True}

    def second_factor_hook(self, user: User) -> Challenge | None:
        return Challenge(kind="totp", pending_token=f"pending-{user.id}")


fixture_extension = FixtureExtension()
