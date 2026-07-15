"""Superadmin integrity report (Phase 6 §6.6).

A single read-only endpoint running the three drift checks and logging a warning per
non-clean result. Report-only: nothing here mutates data or deletes blobs (the orphan
scan is invoked with deletion off regardless of the GC config flag)."""
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SuperAdminUser, get_session
from app.core.storage import BlobStorage, get_storage
from app.services.integrity import check_rollup_drift, check_seq_high_water
from app.services.plugin_maintenance import scan_orphan_blobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/integrity")
async def integrity_report(
    _user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> dict:
    """Run the report-only integrity checks: PluginRunDaily rollup recompute-and-compare,
    seq-allocator high-water invariants, and an orphan-blob count. Superadmin only."""
    now = datetime.now(UTC)
    rollup = await check_rollup_drift(session, now)
    seq = await check_seq_high_water(session)
    # Report-only from this route: never delete, whatever BLOB_GC_DELETE_ENABLED says.
    orphans = await scan_orphan_blobs(session, storage, now, delete_enabled=False)

    if rollup["mismatches"]:
        logger.warning(
            "integrity: %d PluginRunDaily rollup row(s) drifted from recompute",
            rollup["mismatches"],
        )
    if seq["violations"]:
        logger.warning(
            "integrity: %d seq high-water invariant violation(s)", seq["violations"]
        )
    if orphans["orphans"]:
        logger.warning(
            "integrity: %d orphan blob(s) (%d bytes)",
            orphans["orphans"],
            orphans["orphan_bytes"],
        )

    return {
        "checked_at": now.isoformat(),
        "rollup_drift": rollup,
        "seq_high_water": seq,
        "orphan_blobs": orphans,
    }
