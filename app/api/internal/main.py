from fastapi import APIRouter

from app.api.internal.routes import plugin_runner, plugin_runtime

api_router = APIRouter()
api_router.include_router(plugin_runner.router)
api_router.include_router(plugin_runtime.router)
