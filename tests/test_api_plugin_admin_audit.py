"""Admin-action audit: who enabled/configured a plugin is recorded, and secret
values never appear in the audit trail.
"""
from httpx import AsyncClient
from sqlmodel import select

from app.models.audit import Audit

from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _register_runner,
)


def _org_h(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _audits(session, object_type: str) -> list[Audit]:
    return list(
        (
            await session.execute(
                select(Audit).where(Audit.object_type == object_type)
            )
        ).scalars().all()
    )


async def test_enable_and_auto_run_are_audited(
    client: AsyncClient, session, org_a, admin_token,
):
    await _register_runner(client, admin_token, RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _org_h(admin_token, org_a.id)
    plugin_id = SAMPLE_MANIFEST["id"]

    assert (await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)).status_code == 200
    assert (
        await client.post(f"/api/v1/plugins/{plugin_id}/auto-run/enable", headers=h)
    ).status_code == 200

    rows = await _audits(session, "plugin")
    actions = {r.action for r in rows if r.object_id == plugin_id}
    assert "enable" in actions
    assert "auto_run_enable" in actions


async def test_config_change_audits_secret_key_names_only(
    client: AsyncClient, session, org_a, admin_token,
):
    await _register_runner(client, admin_token, RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _org_h(admin_token, org_a.id)
    plugin_id = SAMPLE_MANIFEST["id"]

    r = await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {"threshold": 50}, "secrets": {"api_key": "super-secret-value"}},
        headers=h,
    )
    assert r.status_code == 200, r.text

    rows = await _audits(session, "plugin_config")
    assert rows, "expected a plugin_config audit row"
    row = rows[0]
    assert row.action == "config_update"
    # Secret key name is recorded, value is not.
    assert "api_key" in row.details["secrets_changed"]
    assert "super-secret-value" not in str(row.details)
    assert row.details["settings_changed"]["threshold"]["to"] == 50
