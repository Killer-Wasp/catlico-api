"""F4: MISP import/export service stubs.

Real implementation would call the MISP REST API with the configured server
credentials. These stubs exist to unblock the API surface; integration with
actual MISP instances is a follow-up connector-level task.
"""

import logging

logger = logging.getLogger(__name__)


async def import_from_misp(server_id, event_id, organisation_id, session) -> dict:
    """Stub: import MISP event attributes as alert observables (F4).
    Real impl: call MISP REST API, map attributes → alerts/observables."""
    logger.info("MISP import stub called for server=%s event=%s", server_id, event_id)
    return {"imported": 0, "message": "MISP import not yet implemented"}


async def test_connection(server) -> dict:
    """Stub: test MISP server connectivity."""
    logger.info("MISP test connection stub called for server=%s", server.id)
    return {"ok": True, "message": "Connection test not yet implemented"}


async def export_case_to_misp(server_id, case_id, ioc_only, session) -> dict:
    """Stub: export case IOCs to MISP event."""
    logger.info("MISP export stub called for case=%s server=%s", case_id, server_id)
    return {"exported": 0, "message": "MISP export not yet implemented"}
