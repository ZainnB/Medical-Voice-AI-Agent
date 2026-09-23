"""Application exceptions and handlers that render every error in the {data, error} envelope."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import Envelope, ErrorBody, ErrorDetail, validation_error_details

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected errors raised by services and routes."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: list[ErrorDetail] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


def error_response(
    status_code: int, code: str, message: str, details: list[ErrorDetail] | None = None
) -> JSONResponse:
    """Build a JSON error response in the standard envelope."""
    body = Envelope[None](error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


_HTTP_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no response ever leaves the API outside the envelope."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        if any(error["type"] == "json_invalid" for error in errors):
            return error_response(400, "malformed_json", "Request body is not valid JSON")
        details = validation_error_details(errors)
        if all(error["loc"][0] in ("query", "path") for error in errors):
            return error_response(400, "bad_request", "Invalid query or path parameter", details)
        return error_response(422, "validation_error", "Some fields are invalid", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("db_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
        return error_response(503, "database_unavailable", "The database is temporarily unavailable")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
        return error_response(500, "internal_error", "Something went wrong on our side")
