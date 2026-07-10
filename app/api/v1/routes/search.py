"""Global search: GET /api/v1/search.

Federated Postgres search across cases, alerts, observables, tasks, comments —
one query per entity type, each reusing that type's existing visibility
predicate. Spec: docs/global-search-design.md (catlico workspace root).

Task 5 wires up the case bucket; Task 6 adds alert + task. Tasks 7-8 add
comment and observable buckets to `global_search` below.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import search as search_crud
from app.models.search import SearchCounts, SearchResponse, SearchResults

router = APIRouter(tags=["search"])

SearchType = Literal["case", "alert", "observable", "task", "comment"]


@router.get("/search", response_model=SearchResponse)
async def global_search(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(max_length=200)],
    types: Annotated[list[SearchType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    group_observables: bool = False,
) -> SearchResponse:
    """Federated search across the active org's visible entities. `limit` and
    `offset` apply per entity type. `group_observables` switches the
    observable bucket to one-row-per-value with occurrence counts (palette
    mode). Queries under 2 chars return the empty shape without touching the
    DB."""
    counts = SearchCounts()
    results = SearchResults()
    query = q.strip()
    if len(query) < 2:
        return SearchResponse(counts=counts, results=results)

    wanted = set(types or ["case", "alert", "observable", "task", "comment"])
    org = ctx.organisation_id

    if "case" in wanted:
        results.case, counts.case = await search_crud.search_cases(
            session, org, query, skip=offset, limit=limit
        )
    if "alert" in wanted:
        results.alert, counts.alert = await search_crud.search_alerts(
            session, org, query, skip=offset, limit=limit
        )
    if "task" in wanted:
        results.task, counts.task = await search_crud.search_tasks(
            session, org, query, skip=offset, limit=limit
        )
    if "comment" in wanted:
        results.comment, counts.comment = await search_crud.search_comments(
            session, org, query, skip=offset, limit=limit
        )
    return SearchResponse(counts=counts, results=results)
