from enum import Enum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TaggableType(str, Enum):
    case = "case"
    observable = "observable"
    alert = "alert"
    case_template = "case_template"


class Tag(SQLModel, table=True):
    """Global shared vocabulary, auto-created on first use. Identity is the
    (namespace, predicate, value) triple. Free tags have namespace/value NULL."""

    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("namespace", "predicate", "value", name="uq_tag_identity"),
    )

    # Absent namespace/value stored as "" (not NULL) so the unique constraint is
    # airtight — SQL treats NULLs as distinct, which would let free tags duplicate.
    id: int | None = Field(default=None, primary_key=True)
    namespace: str = Field(default="")
    predicate: str
    value: str = Field(default="")
    description: str = Field(default="")
    colour: str = Field(default="#000000")


class Tagging(SQLModel, table=True):
    """Polymorphic link from a taggable entity to a tag."""

    __tablename__ = "tagging"

    tag_id: int = Field(foreign_key="tag.id", primary_key=True, ondelete="CASCADE")
    taggable_type: TaggableType = Field(primary_key=True)
    taggable_id: str = Field(primary_key=True, index=True)


def parse_tag(text: str) -> tuple[str, str, str]:
    """Parse a tag string into (namespace, predicate, value); absent parts are "".

    "phishing"                  -> ("", "phishing", "")
    "tlp:amber"                 -> ("tlp", "amber", "")
    "kill-chain:phase=exploit"  -> ("kill-chain", "phase", "exploit")
    """
    text = text.strip()
    if not text:
        raise ValueError("Tag cannot be empty")
    namespace = ""
    value = ""
    if ":" in text:
        namespace, rest = text.split(":", 1)
        namespace = namespace.strip()
    else:
        rest = text
    if "=" in rest:
        predicate, value = rest.split("=", 1)
        value = value.strip()
    else:
        predicate = rest
    predicate = predicate.strip()
    if not predicate:
        raise ValueError(f"Tag '{text}' has no predicate")
    return namespace, predicate, value


def tag_to_string(tag: Tag) -> str:
    s = tag.predicate
    if tag.namespace:
        s = f"{tag.namespace}:{s}"
    if tag.value:
        s = f"{s}={tag.value}"
    return s


class TagPublic(SQLModel):
    id: int
    namespace: str
    predicate: str
    value: str
    description: str
    colour: str
    tag: str  # the rendered string form


class TagCreate(SQLModel):
    namespace: str = ""
    predicate: str
    value: str = ""
    description: str = ""
    colour: str = "#000000"


class TagUpdate(SQLModel):
    description: str | None = None
    colour: str | None = None


class TagSetRequest(SQLModel):
    """Replace-semantics body for PUT /{entity}/{id}/tags."""

    tags: list[str]
