import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.internal.main import api_router as internal_api_router
from app.api.v1.main import api_router
from app.core.configs import settings
from app.core.context import RequestIdMiddleware
from app.core.db import AsyncSessionLocal, init_db, run_migrations
from app.crud.audit import dispatch_pending_outbox, register_consumer
from app.services.notifier_delivery import notifier_delivery_consumer
from app.services.outbox_events import notify_feed_consumer
from app.services.outbox_maintenance import run_outbox_maintenance_sweep
from app.services.plugin_dispatch import plugin_event_consumer, push_pending_deliveries
from app.services.plugin_maintenance import run_maintenance_sweep
from app.services.websocket_hub import ws_broadcast_consumer

logger = logging.getLogger(__name__)

#: How often the outbox poller drains undelivered rows, in seconds.
OUTBOX_POLL_INTERVAL = 5.0


def validate_runtime_settings() -> None:
    key = settings.SECRET_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("SECRET_ENCRYPTION_KEY must be set.")
    try:
        Fernet(key.encode())
    except Exception as exc:
        raise RuntimeError("SECRET_ENCRYPTION_KEY is not a valid Fernet key.") from exc


async def _outbox_poller() -> None:
    """Post-commit fan-out: drain the audit outbox on its own session, handing each
    undelivered row to every registered consumer (in-app feed, notifier delivery, WS
    broadcast, plugin dispatch) and marking it delivered once they all succeed."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await dispatch_pending_outbox(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — poller must never die on a transient error
            logger.exception("outbox poller iteration failed")
        await asyncio.sleep(OUTBOX_POLL_INTERVAL)


async def _plugin_maintenance_poller() -> None:
    """Reap stuck plugin runs, mark silent runners offline, roll up usage stats,
    and prune the audit outbox + read notifications + delivery ledger past their
    retention windows (separate sessions/commits so one sweep can't roll back the
    other)."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await run_maintenance_sweep(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — poller must never die on a transient error
            logger.exception("plugin maintenance sweep failed")
        try:
            async with AsyncSessionLocal() as session:
                await run_outbox_maintenance_sweep(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — poller must never die on a transient error
            logger.exception("outbox maintenance sweep failed")
        await asyncio.sleep(settings.PLUGIN_MAINTENANCE_INTERVAL_SECONDS)


async def _plugin_push_poller() -> None:
    """Push queued plugin events to runners with retry/backoff."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await push_pending_deliveries(session)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — poller must never die on a transient error
            logger.exception("plugin push poller failed")
        await asyncio.sleep(settings.PLUGIN_PUSH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_settings()
    if settings.ENVIRONMENT == "local":
        # Local dev convenience: bring the schema to head automatically so
        # `make dev` works on a fresh checkout without a manual `make migrate`.
        logger.info("ENVIRONMENT=local — applying Alembic migrations to head")
        await asyncio.to_thread(run_migrations)
    async with AsyncSessionLocal() as session:
        await init_db(session)
    register_consumer(notify_feed_consumer)
    register_consumer(notifier_delivery_consumer)
    register_consumer(ws_broadcast_consumer)
    register_consumer(plugin_event_consumer)
    poller = asyncio.create_task(_outbox_poller())
    maintenance_poller = asyncio.create_task(_plugin_maintenance_poller())
    push_poller = asyncio.create_task(_plugin_push_poller())
    try:
        yield
    finally:
        poller.cancel()
        maintenance_poller.cancel()
        push_poller.cancel()
        with suppress(asyncio.CancelledError):
            await poller
        with suppress(asyncio.CancelledError):
            await maintenance_poller
        with suppress(asyncio.CancelledError):
            await push_poller


_docs_url = "/docs" if settings.ENVIRONMENT != "production" else None
_redoc_url = "/redoc" if settings.ENVIRONMENT != "production" else None
_openapi_url = "/openapi.json" if settings.ENVIRONMENT != "production" else None

app = FastAPI(
    lifespan=lifespan,
    title="Catlico API",
    description="Backend API for Catlico.",
    version="0.1.0",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(RequestIdMiddleware)
app.include_router(api_router, prefix="/api/v1")
app.include_router(internal_api_router, prefix="/api/internal")
