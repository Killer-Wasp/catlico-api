"""Case metrics route aliases."""

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate
from app.models.metric import Metric


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def test_case_metrics_available_under_cases_route(
    client: AsyncClient,
    session,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="metric case"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    metric = Metric(
        organisation_id=org_a.id,
        name="Risk",
        data_type="number",
        created_by=str(analyst_a.id),
    )
    session.add(metric)
    await session.flush()

    h = _headers(analyst_a_token, org_a.id)
    updated = await client.put(
        f"/api/v1/cases/{case.id}/metrics",
        json={"metrics": {str(metric.id): "7"}},
        headers=h,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()[0]["value"] == "7"

    legacy = await client.get(f"/api/v1/metrics/cases/{case.id}/metrics", headers=h)
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()[0]["metric_name"] == "Risk"
