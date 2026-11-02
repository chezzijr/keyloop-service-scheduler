from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app import errors

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/ready", response_model=None)
async def ready(request: Request) -> dict[str, str] | Response:
    """Readiness: the database answers SELECT 1. Any failure is 503."""
    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return errors.problem(
            request, 503, "service-unavailable", "Service unavailable", errors.DB_UNREACHABLE
        )
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus text format, from the default registry."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
