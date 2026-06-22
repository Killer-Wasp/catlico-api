from fastapi import APIRouter

from app.api.internal.routes import analyzer

api_router = APIRouter()
api_router.include_router(analyzer.router)
