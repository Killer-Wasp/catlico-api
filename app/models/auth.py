import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_token"

    token: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
