"""memvault-os API — FastAPI app entry.

Standalone deployment of the memvault module extracted from the workshop
monorepo. Mounts memvault routes (CRUD + KG + GRC) under `/api/memvault`
and exposes liveness/readiness probes for container orchestration.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.config_stub import settings
from src.events_stub import event_bus
from src.memvault import router as memvault_router
from src.shared.database import async_session_factory
from src.shared.errors import WorkshopError
from src.shared.qdrant_client import get_qdrant_client
from src.shared.redis import get_redis

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.debug:
        settings.validate_secret_key()

    # Wire reactive event handlers (auto-evolve KG, internal flywheel).
    import src.memvault.events  # noqa: F401  registers @event_bus.subscribe handlers

    from src.memvault.kg_auto_evolve import register_auto_evolve_handler

    register_auto_evolve_handler()

    await event_bus.start()
    yield
    await event_bus.stop()


app = FastAPI(title="memvault-os", version="0.1.0", lifespan=lifespan)


# --- Exception handlers ----------------------------------------------------


@app.exception_handler(WorkshopError)
async def workshop_error_handler(request: Request, exc: WorkshopError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Conflict: duplicate or constraint violation", "code": "conflict"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    _log.error("Unhandled exception on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "code": "internal.error"},
    )


# --- Middleware ------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health probes ---------------------------------------------------------


@app.get("/health/liveliness", tags=["health"])
async def liveliness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/readiness", tags=["health"])
async def readiness() -> JSONResponse:
    checks: dict[str, str] = {}
    ok = True

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        ok = False
        checks["database"] = f"error: {exc.__class__.__name__}"

    try:
        redis_client = get_redis()
        pong = await redis_client.ping()
        checks["redis"] = "ok" if pong else "error: no pong"
        if not pong:
            ok = False
    except Exception as exc:
        ok = False
        checks["redis"] = f"error: {exc.__class__.__name__}"

    try:
        qdrant = get_qdrant_client()
        await qdrant.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:
        ok = False
        checks["qdrant"] = f"error: {exc.__class__.__name__}"

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )


# --- Routers ---------------------------------------------------------------
# memvault/__init__.py already chains kg_router and grc_router into the
# top-level memvault router, so a single mount covers all 66 routes.

app.include_router(memvault_router, prefix="/api/memvault", tags=["memvault"])
