"""Global search: GET /api/v1/search.

Federated Postgres search across cases, alerts, observables, tasks, comments.
Spec: docs/global-search-design.md (catlico workspace root)."""

import uuid

from httpx import AsyncClient

from app.crud import alert as alert_crud
from app.crud import case_ as case_crud
from app.crud import comment as comment_crud
from app.crud import observable as obs_crud
from app.crud import task as task_crud
from app.models.alert import AlertCreate
from app.models.case_ import CaseCreate
from app.models.comment import CommentCreate, CommentEntityType
from app.models.observable import ObservableCreate
from app.models.task import TaskCreate


def _headers(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def _seed_case(session, org, builtin_roles, created_by, *, title, description=""):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title, description=description),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(created_by),
    )


async def _seed_observable(session, case, org, created_by, *, type_="ip", data, message=""):
    return await obs_crud.create_case_observable(
        session,
        ObservableCreate(observable_type=type_, data=data, message=message),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(created_by),
    )


class TestObservableIpColumn:
    async def test_ip_address_populates_ip(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.1.2.3")
        assert obs.ip == "10.1.2.3"

    async def test_cidr_data_populates_ip_as_network(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.0.0.0/24")
        assert obs.ip == "10.0.0.0/24"

    async def test_netmask_notation_normalises(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.0.0.0/255.0.0.0")
        assert obs.ip == "10.0.0.0/8"

    async def test_garbage_ip_data_leaves_null(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="not-an-ip")
        assert obs.ip is None

    async def test_non_ip_type_leaves_null(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, type_="domain", data="10.1.2.3")
        assert obs.ip is None
