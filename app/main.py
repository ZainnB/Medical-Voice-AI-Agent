"""FastAPI application factory: logging, routers, middleware, and startup tasks."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api import dashboard, patients
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.seed import seed_demo_patients

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup (no migrations, given the time box) and optionally seed demo data."""
    Base.metadata.create_all(engine)
    if get_settings().seed_on_startup:
        with SessionLocal() as db:
            seed_demo_patients(db)
    logger.info("startup_complete", extra={"fields": {"db_dialect": engine.dialect.name}})
    yield


async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Log method, path, status, and latency for every request."""
    start = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "request",
        extra={
            "fields": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - start) * 1000, 1),
            }
        },
    )
    return response


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="Riverside Family Health: Patient Registration API",
        description="Patient records collected by the Riley voice intake agent (Vapi).",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.middleware("http")(log_requests)
    register_exception_handlers(app)
    app.include_router(dashboard.router)
    app.include_router(patients.router)
    return app


app = create_app()
