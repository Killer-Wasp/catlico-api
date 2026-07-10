from fastapi import APIRouter

from app.api.internal.routes import analyzer, plugin_runner, plugin_runtime, responder

api_router = APIRouter()
api_router.include_router(analyzer.router)
api_router.include_router(plugin_runner.router)
api_router.include_router(plugin_runtime.router)
api_router.include_router(responder.router)
