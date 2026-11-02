"""Health check and the static web dashboard."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import engine, get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


@router.get("/health")
def health(db: Session = Depends(get_db)) -> JSONResponse:
    """Report service and database health, the database engine, and the deployed commit.

    Returns 503 if the database is unreachable.
    """
    info = {"db_engine": engine.dialect.name, "commit": get_settings().render_git_commit or None}
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("health_check_db_failed")
        return JSONResponse(
            status_code=503,
            content={
                "data": {"status": "degraded", "db": "unreachable", **info},
                "error": {"code": "db_unavailable", "message": "Database is unreachable", "details": []},
            },
        )
    return JSONResponse(content={"data": {"status": "ok", "db": "ok", **info}, "error": None})


@router.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """Single-page registry UI (static HTML + JS reading the open GET endpoints)."""
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Send visitors of the bare URL to the dashboard."""
    return RedirectResponse("/dashboard")
