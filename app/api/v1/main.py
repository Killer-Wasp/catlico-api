from fastapi import APIRouter

from app.api.v1.routes import (
    alerts,
    api_keys,
    audit,
    auth,
    case_templates,
    cases,
    comments,
    connectors,
    custom_fields,
    enrichment_jobs,
    functions,
    knowledge_bases,
    logs,
    notifications,
    observable_types,
    observables,
    organisations,
    patterns,
    roles,
    slas,
    tags,
    tasks,
    users,
    ws,
)

api_router = APIRouter()
api_router.include_router(audit.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organisations.router)
api_router.include_router(roles.router)
api_router.include_router(tags.router)
api_router.include_router(cases.router)
api_router.include_router(tasks.router)
api_router.include_router(tasks.queue_router)
api_router.include_router(logs.router)
api_router.include_router(alerts.router)
api_router.include_router(observables.router)
api_router.include_router(observable_types.router)
api_router.include_router(comments.router)
api_router.include_router(case_templates.router)
api_router.include_router(connectors.router)
api_router.include_router(enrichment_jobs.router)
api_router.include_router(functions.router)
api_router.include_router(custom_fields.router)
api_router.include_router(api_keys.router)
api_router.include_router(slas.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(notifications.router)
api_router.include_router(patterns.router)
api_router.include_router(ws.router)
