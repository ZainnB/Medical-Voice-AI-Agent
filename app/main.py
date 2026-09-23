"""FastAPI application factory: logging, routers, middleware, and startup tasks."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api import dashboard
from app.config import get_settings
from app.db import Base, engine
from app.logging_config import configure_logging

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup (no migrations, given the time box)."""
    Base.metadata.create_all(engine)
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
    app.include_router(dashboard.router)
    return app


app = create_app()
