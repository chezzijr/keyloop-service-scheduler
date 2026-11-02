"""HTTP layer for book, read and cancel: status codes and problem+json bodies.

Uses the seed data (ids in scripts/seed.py). The clock is fixed at 2026-11-02 12:00 UTC;
bookings are on Tuesday 2026-11-10, on GMT, so UTC equals London local time."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import make_engine
from app.main import app
from app.routers.appointments import get_now
from scripts.seed import seed

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PROBLEM = "application/problem+json"
# seed ids
OIL, BRAKES, ALIGNMENT, EV = 1, 3, 4, 6
CHLOE = 3
LIFT_2 = 2


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded(engine: AsyncEngine, client: AsyncClient) -> AsyncClient:
    async with engine.begin() as conn:
        await seed(conn)
    return client


def body(
    service_type_id: int = OIL,
    start: str = "2026-11-10T09:00:00Z",
    vehicle_id: int = 1,
    customer_id: int = 1,
) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "dealership_id": 1,
        "service_type_id": service_type_id,
        "start": start,
    }


def assert_problem(response: Any, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM
    problem = response.json()
    assert problem["status"] == status
    assert {"type", "title", "detail", "instance"} <= problem.keys()
    return problem


async def test_book_returns_201_with_the_booking(seeded: AsyncClient) -> None:
    response = await seeded.post("/appointments", json=body(BRAKES))
    assert response.status_code == 201, response.text
    data = response.json()
    created_at = data.pop("created_at")
    assert data == {
        "id": 1,
        "customer_id": 1,
        "vehicle_id": 1,
        "dealership_id": 1,
        "service_type_id": BRAKES,
        "bay": {"id": 1, "name": "Lift 1"},
        "technician": {"id": 1, "name": "Alice"},
        "start": "2026-11-10T09:00:00Z",
        "end": "2026-11-10T10:30:00Z",
        "reserved_until": "2026-11-10T10:45:00Z",
        "status": "confirmed",
    }
    assert created_at.endswith("Z")


async def test_get_returns_the_same_body(seeded: AsyncClient) -> None:
    created = (await seeded.post("/appointments", json=body(BRAKES))).json()
    response = await seeded.get(f"/appointments/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_unknown_is_404(seeded: AsyncClient) -> None:
    problem = assert_problem(await seeded.get("/appointments/999"), 404)
    assert problem["instance"] == "/appointments/999"


async def test_cancel_twice_returns_the_same_body(seeded: AsyncClient) -> None:
    created = (await seeded.post("/appointments", json=body())).json()
    first = await seeded.post(f"/appointments/{created['id']}/cancel")
    assert first.status_code == 200
    assert first.json() == created | {"status": "cancelled"}
    second = await seeded.post(f"/appointments/{created['id']}/cancel")
    assert second.status_code == 200
    assert second.json() == first.json()


async def test_cancel_unknown_is_404(seeded: AsyncClient) -> None:
    assert_problem(await seeded.post("/appointments/999/cancel"), 404)


async def test_no_technician_is_409_with_detail(seeded: AsyncClient) -> None:
    assert (await seeded.post("/appointments", json=body(EV, vehicle_id=2))).status_code == 201
    response = await seeded.post("/appointments", json=body(EV, vehicle_id=3, customer_id=2))
    problem = assert_problem(response, 409)
    assert problem == {
        "type": "https://example.com/problems/booking-conflict",
        "title": "Appointment cannot be booked",
        "status": 409,
        "detail": "No technician with skill ev_hv is free from 09:00 to 10:15 UTC on 2026-11-10"
        " (EV battery health check: 60 min work + 15 min buffer).",
        "instance": "/appointments",
        "reason": "no_technician",
    }


async def test_no_bay_is_409(seeded: AsyncClient) -> None:
    assert (await seeded.post("/appointments", json=body(ALIGNMENT))).status_code == 201
    response = await seeded.post("/appointments", json=body(ALIGNMENT, vehicle_id=3, customer_id=2))
    assert assert_problem(response, 409)["reason"] == "no_bay"


async def test_vehicle_busy_is_409_with_appointment_id(seeded: AsyncClient) -> None:
    first = (await seeded.post("/appointments", json=body())).json()
    problem = assert_problem(await seeded.post("/appointments", json=body()), 409)
    assert problem["reason"] == "vehicle_busy"
    assert problem["appointment_id"] == first["id"]


async def test_off_grid_start_is_422_with_errors(seeded: AsyncClient) -> None:
    response = await seeded.post("/appointments", json=body(start="2026-11-10T09:10:00Z"))
    problem = assert_problem(response, 422)
    assert problem["errors"] == [
        {"field": "start", "message": "must be on the 15-minute grid in UTC, with zero seconds"}
    ]
    assert "reason" not in problem


async def test_naive_start_is_422(seeded: AsyncClient) -> None:
    response = await seeded.post("/appointments", json=body(start="2026-11-10T09:00:00"))
    problem = assert_problem(response, 422)
    assert [e["field"] for e in problem["errors"]] == ["start"]


async def test_missing_field_is_422(seeded: AsyncClient) -> None:
    payload = body()
    del payload["vehicle_id"]
    problem = assert_problem(await seeded.post("/appointments", json=payload), 422)
    assert [e["field"] for e in problem["errors"]] == ["vehicle_id"]


async def test_outside_hours_is_422_with_reason(seeded: AsyncClient) -> None:
    response = await seeded.post("/appointments", json=body(start="2026-11-10T17:30:00Z"))
    problem = assert_problem(response, 422)
    assert problem["reason"] == "outside_hours"
    assert [e["field"] for e in problem["errors"]] == ["start"]


async def test_vehicle_of_another_customer_is_422(seeded: AsyncClient) -> None:
    response = await seeded.post("/appointments", json=body(vehicle_id=3, customer_id=1))
    problem = assert_problem(response, 422)
    assert problem["errors"] == [{"field": "vehicle_id", "message": "does not belong to the customer"}]


@pytest.fixture
async def app_engine(client: AsyncClient) -> AsyncIterator[list[AsyncEngine]]:
    """Lets a test put its own engine on app.state; restores the fixture engine after."""
    original = app.state.engine
    holder: list[AsyncEngine] = []
    yield holder
    for eng in holder:
        await eng.dispose()
    app.state.engine = original


async def test_statement_timeout_is_503(
    seeded: AsyncClient, engine: AsyncEngine, migrated_url: str, app_engine: list[AsyncEngine]
) -> None:
    timeout_engine = make_engine(migrated_url, statement_timeout_ms=200)
    app_engine.append(timeout_engine)
    app.state.engine = timeout_engine
    # Chloe is the only ev_hv technician. Hold an uncommitted booking on her: the API's
    # candidate query cannot see it, so the API insert waits on it until the timeout.
    async with engine.connect() as holder:
        await holder.begin()
        try:
            await holder.execute(
                text(
                    "INSERT INTO appointment (customer_id, vehicle_id, dealership_id,"
                    " service_type_id, service_bay_id, technician_id, during, ends_at)"
                    " VALUES (2, 3, 1, :ev, :bay, :tech,"
                    " tstzrange('2026-11-10 09:00Z', '2026-11-10 10:15Z', '[)'),"
                    " '2026-11-10 10:00Z')"
                ),
                {"ev": EV, "bay": LIFT_2, "tech": CHLOE},
            )
            response = await seeded.post("/appointments", json=body(EV, vehicle_id=2))
        finally:
            await holder.rollback()
    assert_problem(response, 503)


async def test_lost_database_is_503(client: AsyncClient, app_engine: list[AsyncEngine]) -> None:
    dead = make_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x")
    app_engine.append(dead)
    app.state.engine = dead
    assert_problem(await client.get("/appointments/1"), 503)


async def test_unknown_database_host_is_503(
    client: AsyncClient, app_engine: list[AsyncEngine]
) -> None:
    dead = make_engine("postgresql+asyncpg://u:p@db-host.invalid:5432/x")
    app_engine.append(dead)
    app.state.engine = dead
    assert_problem(await client.get("/appointments/1"), 503)


async def test_dropped_pooled_connection_is_503(
    seeded: AsyncClient, engine: AsyncEngine, migrated_url: str, app_engine: list[AsyncEngine]
) -> None:
    # one pooled connection, tagged so only it is killed (as after a database restart)
    one = create_async_engine(
        migrated_url,
        pool_size=1,
        max_overflow=0,
        connect_args={"server_settings": {"application_name": "drop_me"}},
    )
    app_engine.append(one)
    app.state.engine = one
    assert (await seeded.post("/appointments", json=body())).status_code == 201
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid, 1000) FROM pg_stat_activity"
                " WHERE application_name = 'drop_me'"
            )
        )
    assert_problem(await seeded.get("/appointments/1"), 503)
