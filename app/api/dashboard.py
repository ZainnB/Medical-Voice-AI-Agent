"""Health check and the static web dashboard."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> JSONResponse:
    """Report service and database health. Returns 503 if the database is unreachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("health_check_db_failed")
        return JSONResponse(
            status_code=503,
            content={
                "data": {"status": "degraded", "db": "unreachable"},
                "error": {"code": "db_unavailable", "message": "Database is unreachable", "details": []},
            },
        )
    return JSONResponse(content={"data": {"status": "ok", "db": "ok"}, "error": None})
