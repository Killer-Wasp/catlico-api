"""F4: MISP export service stub."""

import logging

logger = logging.getLogger(__name__)


async def export_case_to_misp(server_id, case_id, ioc_only, session) -> dict:
    """Stub: export case IOCs to a MISP event."""
    logger.info("MISP export stub called for case=%s server=%s ioc_only=%s", case_id, server_id, ioc_only)
    return {"exported": 0, "message": "MISP export not yet implemented"}
