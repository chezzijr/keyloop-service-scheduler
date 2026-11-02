"""Exception handlers: every error body is RFC 9457 application/problem+json."""

import socket
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.services.booking import AppointmentNotFound, BookingConflict, BookingValidationError
from app.services.rules import FieldError

PROBLEMS = "https://example.com/problems/"
QUERY_CANCELED = "57014"  # statement_timeout
DB_UNREACHABLE = "The database cannot be reached; try again."


def problem(
    request: Request, status: int, slug: str, title: str, detail: str, **extra: Any
) -> JSONResponse:
    content = {
        "type": PROBLEMS + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
    }
    content.update({k: v for k, v in extra.items() if v is not None})
    return JSONResponse(content, status_code=status, media_type="application/problem+json")


def _invalid(request: Request, errors: list[FieldError], reason: str | None = None) -> JSONResponse:
    return problem(
        request,
        422,
        "validation-error",
        "Request is not valid",
        "One or more fields are not valid; see errors.",
        reason=reason,
        errors=[{"field": e.field, "message": e.message} for e in errors],
    )


async def request_invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for e in exc.errors():
        loc = list(e["loc"])
        if loc and loc[0] in ("body", "path", "query"):
            loc = loc[1:]
        errors.append(FieldError(".".join(map(str, loc)) or "body", e["msg"]))
    return _invalid(request, errors)


async def booking_invalid(request: Request, exc: BookingValidationError) -> JSONResponse:
    return _invalid(request, exc.errors, exc.reason)


async def booking_conflict(request: Request, exc: BookingConflict) -> JSONResponse:
    return problem(
        request,
        409,
        "booking-conflict",
        "Appointment cannot be booked",
        exc.detail,
        reason=exc.reason,
        appointment_id=exc.appointment_id,
    )


async def not_found(request: Request, exc: AppointmentNotFound) -> JSONResponse:
    return problem(request, 404, "not-found", "Appointment not found", "No appointment with this id.")


def _unavailable(request: Request, detail: str) -> JSONResponse:
    return problem(request, 503, "service-unavailable", "Service unavailable", detail)


async def database_error(request: Request, exc: DBAPIError) -> JSONResponse:
    if getattr(exc.orig, "sqlstate", None) == QUERY_CANCELED:
        return _unavailable(request, "The database took too long to answer; try again.")
    if exc.connection_invalidated:  # SQLAlchemy saw a disconnect, e.g. after a DB restart
        return _unavailable(request, DB_UNREACHABLE)
    raise exc


# asyncpg's connect raises these as is; SQLAlchemy does not wrap them in DBAPIError
CONNECT_ERRORS = (ConnectionError, socket.gaierror, TimeoutError)


async def connect_failed(request: Request, exc: Exception) -> JSONResponse:
    return _unavailable(request, DB_UNREACHABLE)


def register(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, request_invalid)
    app.add_exception_handler(BookingValidationError, booking_invalid)
    app.add_exception_handler(BookingConflict, booking_conflict)
    app.add_exception_handler(AppointmentNotFound, not_found)
    app.add_exception_handler(DBAPIError, database_error)
    for error in CONNECT_ERRORS:
        app.add_exception_handler(error, connect_failed)
