from fastapi import APIRouter

from app.api.v1.routes import (
    alerts,
    auth,
    case_templates,
    cases,
    comments,
    connectors,
    custom_fields,
    enrichment_jobs,
    logs,
    observables,
    organisations,
    roles,
    tasks,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organisations.router)
api_router.include_router(roles.router)
api_router.include_router(cases.router)
api_router.include_router(tasks.router)
api_router.include_router(logs.router)
api_router.include_router(alerts.router)
api_router.include_router(observables.router)
api_router.include_router(comments.router)
api_router.include_router(case_templates.router)
api_router.include_router(connectors.router)
api_router.include_router(enrichment_jobs.router)
api_router.include_router(custom_fields.router)
