"""Request ids, JSON logs and Prometheus metrics (docs/details/observability.md)."""

import re
import time
import uuid
from typing import Any

import structlog
from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# the method comes from the client, so any other value is labelled "other" (fixed label set)
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Time spent in the app per request",
    ["route", "method", "status"],
)
BOOKED = Counter("appointments_booked_total", "Successful bookings (201)", ["dealership_id"])
REJECTED = Counter("booking_rejected_total", "Bookings rejected with 409, by reason", ["reason"])
CANCELLED = Counter("appointments_cancelled_total", "Cancels that changed a row")

log = structlog.get_logger()


def configure() -> None:
    """One JSON object per line on stdout: ts (ISO, UTC), level, bound context, fields."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


def request_id_from(headers: list[tuple[bytes, bytes]]) -> str:
    """The client's X-Request-ID if it follows the rule, else a new random id."""
    for name, value in headers:
        if name == b"x-request-id":
            sent = value.decode("latin-1")
            if REQUEST_ID.fullmatch(sent):
                return sent
            break
    return uuid.uuid4().hex


class RequestMiddleware:
    """Per request: request id (log context, response header, request.state), one
    access log line and one latency observation, labelled by the route template."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = request_id_from(scope["headers"])
        scope.setdefault("state", {})["request_id"] = rid
        status = 500  # stays 500 if the app raises before it starts a response

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        start = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=rid):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                elapsed = time.perf_counter() - start
                # the router puts the matched route in the scope; no route, no template
                route: Any = scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                method = scope["method"]
                method_label = method if method in METHODS else "other"
                REQUEST_DURATION.labels(template, method_label, str(status)).observe(elapsed)
                log.info(
                    "request",
                    method=method,
                    route=template,
                    status=status,
                    latency_ms=round(elapsed * 1000, 3),
                )
