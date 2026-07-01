import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.api_key import ApiKey, ApiKeyCreate, ApiKeyUpdate

_TOKEN_BYTES = 32
_PREFIX = "thp_"


def _make_token() -> tuple[str, str]:
    raw = secrets.token_hex(_TOKEN_BYTES)
    key = f"{_PREFIX}{raw}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash


async def get_key(session: AsyncSession, key_id: uuid.UUID, organisation_id: str) -> ApiKey | None:
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.organisation_id == organisation_id,
            ApiKey.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_keys(session: AsyncSession, organisation_id: str) -> list[ApiKey]:
    result = await session.execute(
        select(ApiKey)
        .where(
            ApiKey.organisation_id == organisation_id,
            ApiKey.deleted_at.is_(None),
        )
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def create_key(
    session: AsyncSession,
    key_in: ApiKeyCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> tuple[ApiKey, str]:
    plaintext, key_hash = _make_token()
    key = ApiKey(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        name=key_in.name,
        prefix=_PREFIX,
        last_four=plaintext[-4:],
        key_hash=key_hash,
        scopes=key_in.scopes,
        expires_at=key_in.expires_at,
        created_by=created_by,
    )
    session.add(key)
    await session.flush()
    return key, plaintext


async def update_key(
    session: AsyncSession,
    key: ApiKey,
    key_in: ApiKeyUpdate,
    updated_by: str,
) -> ApiKey:
    update_data = key_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(key, k, v)
    key.updated_by = updated_by
    session.add(key)
    await session.flush()
    return key


async def delete_key(
    session: AsyncSession, key: ApiKey, deleted_by: str
) -> None:
    from datetime import UTC, datetime

    key.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    key.deleted_by = deleted_by
    session.add(key)
    await session.flush()


async def get_key_by_hash(session: AsyncSession, key_hash: str) -> ApiKey | None:
    """Look up a non-deleted API key by its SHA-256 hash (C1)."""
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def touch_key(session: AsyncSession, key: ApiKey) -> None:
    """Update last_used_at after successful authentication."""
    from datetime import UTC, datetime

    key.last_used_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(key)
    await session.flush()
