from fastapi import APIRouter

from app.api.internal.routes import analyzer, responder

api_router = APIRouter()
api_router.include_router(analyzer.router)
api_router.include_router(responder.router)
