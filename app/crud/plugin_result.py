"""Read helpers and serialization for the public Plugin Results surface.

Plugin results are append-only evidence attached to a case, alert, observable, or
task. The public read routes (``app/api/v1/routes/plugin_results.py``) resolve entity
visibility first — the entity-read guard is the tenancy boundary — then load results
for that one entity here. Results ride entity visibility: a viewer entitled to see a
shared case sees results on it regardless of which org produced them (the producing
org stays visible in provenance via ``organisation_id``). See the plan's
"Plugin Result And Evidence Model" and "Entity-side surfaces".
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.plugin_runner import PluginResult, PluginRunFile

_FILE_REF_PREFIX = "plugin-run-file:"


async def resolve_entity_attachment(
    session: AsyncSession, entity_type: str, entity_id: str, file_ref: str
) -> PluginRunFile | None:
    """Resolve a plugin attachment ``file_ref`` to its ``PluginRunFile`` **iff** a
    ``PluginResult`` on this exact entity references it.

    The ``file_ref`` arrives from the URL and is never treated as a storage path: it
    is only honoured when some result on ``(entity_type, entity_id)`` — an entity the
    caller has already proven it may read — lists it in ``attachments``. A ref that is
    real but attached elsewhere (an entity the caller cannot read) resolves to ``None``
    here, so an attachment cannot be fetched by guessing its ref. Returns ``None`` for
    a malformed ref, a ref not referenced on this entity, or a missing file row.
    """
    if not isinstance(file_ref, str) or not file_ref.startswith(_FILE_REF_PREFIX):
        return None
    try:
        file_id = uuid.UUID(file_ref.removeprefix(_FILE_REF_PREFIX))
    except ValueError:
        return None
    results = await list_for_entity(session, entity_type, entity_id)
    referenced = any(
        isinstance(att, dict) and att.get("file_ref") == file_ref
        for result in results
        for att in (result.attachments or [])
    )
    if not referenced:
        return None
    return await session.get(PluginRunFile, file_id)


async def list_for_entity(
    session: AsyncSession, entity_type: str, entity_id: str
) -> list[PluginResult]:
    """All results for one entity, newest first.

    Scoped by ``(entity_type, entity_id)`` only — the entity id is globally unique
    per type (observable UUID / case id / alert id / task ``"{case_id}:{task_id}"``
    composite; bare task ids are per-case sequences and would collide), and the
    caller has already proven it may read that specific entity. There is
    deliberately no ``organisation_id`` predicate: results on a shared entity may be
    produced by the owner org, and hiding those would contradict the
    entity-visibility rule.
    """
    result = await session.execute(
        select(PluginResult)
        .where(
            PluginResult.entity_type == entity_type,
            PluginResult.entity_id == entity_id,
        )
        .order_by(PluginResult.created_at.desc(), PluginResult.id.desc())
    )
    return list(result.scalars().all())


def _is_stale(result: PluginResult, now: datetime) -> bool:
    """A result is stale once its ``expires_at`` is in the past. Never hidden — the
    read surface returns expired results (incl. an expired-but-latest one) so the UI
    can flag them; deletion is a retention concern handled elsewhere."""
    expires_at = result.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < now


def public(
    result: PluginResult, *, stale: bool, latest: bool, include_raw: bool
) -> dict:
    data = {
        "id": str(result.id),
        "plugin_run_id": str(result.plugin_run_id) if result.plugin_run_id else None,
        "organisation_id": result.organisation_id,
        "plugin_id": result.plugin_id,
        "plugin_version_id": result.plugin_version_id,
        "entity_type": result.entity_type,
        "entity_id": result.entity_id,
        "source": result.source,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "render_mode": result.render_mode,
        "title": result.title,
        "summary": result.summary,
        "normalized_data": result.normalized_data,
        "result_metadata": result.result_metadata,
        # Attachment metadata is passed through as stored
        # ({file_ref, filename, content_type, size, sha256}). No download URL is
        # synthesized here; the client builds the entity-scoped download path from the
        # file_ref (GET /<entity>/plugin-results/files/{file_ref} — see
        # app/api/v1/routes/plugin_results.py).
        "attachments": result.attachments,
        "fingerprint": result.fingerprint,
        "expires_at": result.expires_at,
        "created_at": result.created_at,
        "stale": stale,
        "latest": latest,
    }
    if include_raw:
        # Original vendor payload — kept for audit/debug, collapsed by default in the
        # UI. Included in the list because per-entity result counts are small and the
        # plan defines no separate detail endpoint to fetch it from.
        data["raw_data"] = result.raw_data
    return data


def serialize_list(results: list[PluginResult], *, include_raw: bool = True) -> list[dict]:
    """Serialize newest-first results, marking the latest per ``(plugin_id, source)``.

    "Latest" is grouped by ``(plugin_id, source)`` because that is the supersession
    key in the evidence model (a newer result from the same plugin+source supersedes
    the older), and the UI groups results by plugin/source and shows the latest first.
    Since ``results`` is already newest-first, the first row seen for each key is its
    latest.
    """
    now = datetime.now(UTC)
    seen_latest: set[tuple[str, str]] = set()
    out: list[dict] = []
    for result in results:
        key = (result.plugin_id, result.source)
        is_latest = key not in seen_latest
        seen_latest.add(key)
        out.append(
            public(
                result,
                stale=_is_stale(result, now),
                latest=is_latest,
                include_raw=include_raw,
            )
        )
    return out
