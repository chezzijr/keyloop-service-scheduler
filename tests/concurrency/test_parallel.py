"""Parallel bookings on real Postgres: every request has its own pooled connection, so the
exclusion constraints, the savepoint loop and the final vehicle re-check all run for real.

Each scenario repeats ROUNDS times to catch an order-dependent result; the appointment
table is emptied before each round so a round never finds the slot already taken."""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import repository
from app.db import make_engine
from app.main import app
from app.routers.appointments import get_now
from scripts.seed import seed

pytestmark = pytest.mark.anyio

ROUNDS = 20
N = 10  # parallel requests in the capacity test
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
START = "2026-11-10T09:00:00Z"  # Tuesday, GMT, inside opening hours
OIL, MOT = 1, 5  # seed service types


@pytest.fixture
async def parallel_client(
    engine: AsyncEngine, client: AsyncClient, migrated_url: str
) -> AsyncIterator[AsyncClient]:
    """Seeded database, fixed clock, and an app engine with a connection per request."""
    async with engine.begin() as conn:
        await seed(conn)
    wide = make_engine(migrated_url, pool_size=N, max_overflow=5)
    original = app.state.engine
    app.state.engine = wide
    app.dependency_overrides[get_now] = lambda: NOW
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        app.state.engine = original
        await wide.dispose()


async def empty_appointments(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE appointment RESTART IDENTITY"))


def booking(service_type_id: int, vehicle_id: int, customer_id: int = 1) -> dict[str, object]:
    return {
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "dealership_id": 1,
        "service_type_id": service_type_id,
        "start": START,
    }


async def overlapping_active_pairs(engine: AsyncEngine) -> int:
    """Pairs of active appointments that share a bay or a technician and overlap."""
    async with engine.connect() as conn:
        return await conn.scalar(
            text(
                "SELECT count(*) FROM appointment a JOIN appointment b ON a.id < b.id"
                " WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'"
                " AND (a.service_bay_id = b.service_bay_id OR a.technician_id = b.technician_id)"
                " AND a.during && b.during"
            )
        )


async def test_capacity_k_gives_exactly_k_bookings(
    parallel_client: AsyncClient, engine: AsyncEngine
) -> None:
    k = 3
    async with engine.begin() as conn:
        # oil change: general skill, lift bay. Leave k qualified technicians (Alice, Ben,
        # Chloe) for the k lifts, and give customer 1 enough vehicles for N requests.
        await conn.execute(text("DELETE FROM technician_skill WHERE skill_id = 1 AND technician_id = 4"))
        await conn.execute(
            text(
                "INSERT INTO vehicle (customer_id, vin, make, model)"
                " SELECT 1, 'PARALLEL' || lpad(g::text, 9, '0'), 'Test', 'Car'"
                " FROM generate_series(1, :extra) g"
            ),
            {"extra": N - 4},
        )
        vehicle_ids = list((await conn.scalars(text("SELECT id FROM vehicle WHERE customer_id = 1"))).all())
        assert len(vehicle_ids) == N - 2  # seed gives customer 1 two vehicles
        # vehicles 3 and 4 belong to customers 2 and 3
        requests = [booking(OIL, v) for v in vehicle_ids] + [
            booking(OIL, 3, customer_id=2),
            booking(OIL, 4, customer_id=3),
        ]
    assert len(requests) == N

    for round_ in range(ROUNDS):
        await empty_appointments(engine)
        responses = await asyncio.gather(
            *(parallel_client.post("/appointments", json=r) for r in requests)
        )
        statuses = Counter(r.status_code for r in responses)
        assert statuses == {201: k, 409: N - k}, (round_, [r.text for r in responses])
        reasons = {r.json()["reason"] for r in responses if r.status_code == 409}
        assert reasons <= {"no_technician", "no_bay"}, round_
        assert await overlapping_active_pairs(engine) == 0, round_


async def test_same_request_twice_gives_one_booking_and_vehicle_busy(
    parallel_client: AsyncClient, engine: AsyncEngine
) -> None:
    # MOT: one qualified technician (Ben) and one MOT lane, so the loser has no other pair
    request = booking(MOT, vehicle_id=1)
    for round_ in range(ROUNDS):
        await empty_appointments(engine)
        first, second = await asyncio.gather(
            parallel_client.post("/appointments", json=request),
            parallel_client.post("/appointments", json=request),
        )
        won, lost = (first, second) if first.status_code == 201 else (second, first)
        assert (won.status_code, lost.status_code) == (201, 409), (round_, first.text, second.text)
        assert lost.json()["reason"] == "vehicle_busy", (round_, lost.text)
        assert lost.json()["appointment_id"] == won.json()["id"], round_
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM appointment")) == 1, round_


async def test_booking_waits_for_the_vehicle_lock(
    engine: AsyncEngine, client: AsyncClient, migrated_url: str
) -> None:
    """The same vehicle booked at two dealerships at once must not deadlock on
    no_vehicle_overlap (the dealership lock does not cover it), so every booking also
    takes a lock on its vehicle. Here another transaction holds vehicle 1's lock, so a
    booking for vehicle 1 waits until the statement timeout (503) instead of inserting."""
    async with engine.begin() as conn:
        await seed(conn)
    short = make_engine(migrated_url, statement_timeout_ms=200)
    original = app.state.engine
    app.state.engine = short
    app.dependency_overrides[get_now] = lambda: NOW
    try:
        async with AsyncSession(engine) as holder:
            async with holder.begin():
                await repository.lock_vehicle(holder, 1)
                response = await client.post("/appointments", json=booking(OIL, vehicle_id=1))
        assert response.status_code == 503, response.text
    finally:
        app.dependency_overrides.clear()
        app.state.engine = original
        await short.dispose()
