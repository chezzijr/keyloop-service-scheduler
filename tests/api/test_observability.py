"""Request ids, access logs, metrics and readiness (docs/design.md Observability).

The Prometheus registry is process-global, so counters are read as a change between
before and after, never as an absolute value."""

import json
import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from app.db import make_engine
from app.main import app
from app.routers.appointments import get_now
from scripts.seed import seed

pytestmark = pytest.mark.anyio

PROBLEM = "application/problem+json"
ID_RULE = re.compile(r"[A-Za-z0-9._-]{1,64}")
EV = 6  # seed service type with one qualified technician


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded(engine: AsyncEngine, client: AsyncClient) -> AsyncClient:
    async with engine.begin() as conn:
        await seed(conn)
    return client


def booking(service_type_id: int = 1, vehicle_id: int = 1, customer_id: int = 1) -> dict:
    return {
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "dealership_id": 1,
        "service_type_id": service_type_id,
        "start": "2026-11-10T09:00:00Z",
    }


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# request id


async def test_valid_request_id_is_echoed(client: AsyncClient) -> None:
    rid = "abc.DEF_123-x"
    response = await client.get("/health", headers={"X-Request-ID": rid})
    assert response.headers["x-request-id"] == rid


@pytest.mark.parametrize("sent", ["a" * 65, "bad id", "semi;colon", "", None])
async def test_invalid_or_missing_request_id_is_replaced(
    client: AsyncClient, sent: str | None
) -> None:
    headers = {} if sent is None else {"X-Request-ID": sent}
    response = await client.get("/health", headers=headers)
    rid = response.headers["x-request-id"]
    assert rid != sent
    assert ID_RULE.fullmatch(rid)


async def test_64_char_request_id_is_kept(client: AsyncClient) -> None:
    rid = "a" * 64
    response = await client.get("/health", headers={"X-Request-ID": rid})
    assert response.headers["x-request-id"] == rid


async def test_problem_body_carries_the_request_id(client: AsyncClient) -> None:
    response = await client.get("/appointments/999", headers={"X-Request-ID": "trace-1"})
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["request_id"] == "trace-1"
    assert response.headers["x-request-id"] == "trace-1"


async def test_generated_request_id_matches_problem_body(client: AsyncClient) -> None:
    response = await client.get("/appointments/999")
    assert response.json()["request_id"] == response.headers["x-request-id"]


# readiness


async def test_ready_with_database_up(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.fixture
async def dead_engine(client: AsyncClient) -> AsyncIterator[None]:
    original = app.state.engine
    dead = make_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x")
    app.state.engine = dead
    yield
    await dead.dispose()
    app.state.engine = original


async def test_ready_with_database_down_is_503(client: AsyncClient, dead_engine: None) -> None:
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["status"] == 503


async def test_health_does_not_need_the_database(
    client: AsyncClient, dead_engine: None
) -> None:
    assert (await client.get("/health")).status_code == 200


# metrics


async def test_metrics_lists_the_four_metrics(client: AsyncClient) -> None:
    await client.get("/health")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    for name in (
        "http_request_duration_seconds",
        "appointments_booked_total",
        "booking_rejected_total",
        "appointments_cancelled_total",
    ):
        assert f"# TYPE {name} " in text, name
    assert "booking_insert_attempts" not in text


async def test_unknown_url_is_labelled_unmatched(client: AsyncClient) -> None:
    labels = {"route": "unmatched", "method": "GET", "status": "404"}
    before = sample("http_request_duration_seconds_count", **labels)
    assert (await client.get("/nope")).status_code == 404
    assert sample("http_request_duration_seconds_count", **labels) == before + 1
    assert 'route="/nope"' not in (await client.get("/metrics")).text


async def test_route_label_is_the_template(client: AsyncClient) -> None:
    labels = {"route": "/appointments/{appointment_id}", "method": "GET", "status": "404"}
    before = sample("http_request_duration_seconds_count", **labels)
    await client.get("/appointments/4242")
    assert sample("http_request_duration_seconds_count", **labels) == before + 1
    assert "/appointments/4242" not in (await client.get("/metrics")).text


async def test_booking_201_counts_per_dealership(seeded: AsyncClient) -> None:
    before = sample("appointments_booked_total", dealership_id="1")
    assert (await seeded.post("/appointments", json=booking())).status_code == 201
    assert sample("appointments_booked_total", dealership_id="1") == before + 1


async def test_booking_409_counts_by_reason(seeded: AsyncClient) -> None:
    assert (await seeded.post("/appointments", json=booking(EV, vehicle_id=2))).status_code == 201
    before = sample("booking_rejected_total", reason="no_technician")
    response = await seeded.post("/appointments", json=booking(EV, vehicle_id=3, customer_id=2))
    assert response.status_code == 409
    assert sample("booking_rejected_total", reason="no_technician") == before + 1


async def test_cancel_twice_counts_once(seeded: AsyncClient) -> None:
    created = (await seeded.post("/appointments", json=booking())).json()
    before = sample("appointments_cancelled_total")
    assert (await seeded.post(f"/appointments/{created['id']}/cancel")).status_code == 200
    assert (await seeded.post(f"/appointments/{created['id']}/cancel")).status_code == 200
    assert sample("appointments_cancelled_total") == before + 1


# logs


async def test_access_log_line_has_the_fields(client: AsyncClient) -> None:
    with capture_logs(processors=[merge_contextvars]) as logs:
        await client.get("/appointments/999", headers={"X-Request-ID": "log-1"})
    access = [e for e in logs if e.get("event") == "request"]
    assert len(access) == 1
    line = access[0]
    assert line["request_id"] == "log-1"
    assert line["method"] == "GET"
    assert line["route"] == "/appointments/{appointment_id}"
    assert line["status"] == 404
    assert line["log_level"] == "info"
    assert isinstance(line["latency_ms"], float) and line["latency_ms"] >= 0


async def test_booking_logs_its_outcome(seeded: AsyncClient) -> None:
    with capture_logs(processors=[merge_contextvars]) as logs:
        await seeded.post("/appointments", json=booking(), headers={"X-Request-ID": "b-1"})
    outcome = [e for e in logs if e.get("event") == "appointment_booked"]
    assert len(outcome) == 1
    assert outcome[0]["appointment_id"] == 1
    assert outcome[0]["attempts"] == 1


async def test_log_line_is_json_on_stdout(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await client.get("/health", headers={"X-Request-ID": "json-1"})
    lines = [json.loads(s) for s in capsys.readouterr().out.splitlines() if s.startswith("{")]
    line = next(e for e in lines if e.get("request_id") == "json-1")
    assert line["level"] == "info"
    assert line["route"] == "/health"
    assert line["status"] == 200
    assert datetime.fromisoformat(line["ts"]).utcoffset().total_seconds() == 0
    assert {"method", "latency_ms"} <= line.keys()


async def test_unknown_http_method_is_labelled_other(client: AsyncClient) -> None:
    # the method comes from the client; a free-form label would grow without limit
    labels = {"route": "/health", "method": "other", "status": "405"}
    before = sample("http_request_duration_seconds_count", **labels)
    assert (await client.request("BREW", "/health")).status_code == 405
    assert sample("http_request_duration_seconds_count", **labels) == before + 1
    assert 'method="BREW"' not in (await client.get("/metrics")).text
