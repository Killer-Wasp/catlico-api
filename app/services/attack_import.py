"""MITRE ATT&CK catalog import: fetch the CTI enterprise-attack STIX bundle
and parse its attack-pattern objects into PatternImportItems.

Parsing is separated from fetching so tests never touch the network — the
route calls ``fetch_attack_bundle`` (monkeypatched in tests) then
``parse_attack_bundle`` (pure).
"""
from typing import Any

import httpx

from app.core.configs import settings
from app.models.pattern import PatternImportItem


async def fetch_attack_bundle() -> dict[str, Any]:
    """Download the enterprise-attack STIX bundle (~40MB)."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(settings.ATTACK_BUNDLE_URL)
        resp.raise_for_status()
        return resp.json()


def parse_attack_bundle(bundle: dict[str, Any]) -> list[PatternImportItem]:
    """STIX attack-pattern objects -> import items, keyed by ATT&CK external id.

    Skips revoked/deprecated objects and anything without a mitre-attack
    external reference. Sub-technique parentage is derived from the id shape
    (T1078.001 -> T1078)."""
    items: list[PatternImportItem] = []
    for obj in bundle.get("objects") or []:
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        ref = next(
            (r for r in obj.get("external_references") or []
             if r.get("source_name") == "mitre-attack" and r.get("external_id")),
            None,
        )
        if ref is None:
            continue
        external_id: str = ref["external_id"]
        tactics = [
            p["phase_name"]
            for p in obj.get("kill_chain_phases") or []
            if p.get("kill_chain_name") == "mitre-attack" and p.get("phase_name")
        ]
        items.append(
            PatternImportItem(
                external_id=external_id,
                name=obj.get("name", external_id),
                description=obj.get("description", ""),
                tactics=tactics,
                url=ref.get("url", ""),
                parent_external_id=(
                    external_id.split(".")[0] if "." in external_id else None
                ),
            )
        )
    return items
