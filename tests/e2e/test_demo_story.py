"""The scripts/demo.sh story, run in-process on the seed data with a fixed clock.

Clock: 2026-11-02 12:00 UTC. Bookings on Tuesday 2026-11-10 09:00 UTC (GMT, so London
local time is the same). Seed ids are listed in scripts/seed.py."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.main import app
from app.routers.appointments import get_now
from scripts.seed import seed

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
START = "2026-11-10T09:00:00Z"
OIL, BRAKES, EV = 1, 3, 6


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.clear()


def body(service_type_id: int, vehicle_id: int, customer_id: int, start: str = START) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "dealership_id": 1,
        "service_type_id": service_type_id,
        "start": start,
    }


async def test_demo_story(engine: AsyncEngine, client: AsyncClient) -> None:
    async with engine.begin() as conn:
        await seed(conn)

    # 1 book brake pads for vehicle 1
    booked = await client.post("/appointments", json=body(BRAKES, vehicle_id=1, customer_id=1))
    assert booked.status_code == 201, booked.text
    first = booked.json()
    assert first["status"] == "confirmed"

    # 2 read it back
    read = await client.get(f"/appointments/{first['id']}")
    assert read.status_code == 200
    assert read.json() == first

    # 3 same vehicle, overlapping time: points at the booking it already has
    clash = await client.post(
        "/appointments", json=body(OIL, vehicle_id=1, customer_id=1, start="2026-11-10T09:30:00Z")
    )
    assert clash.status_code == 409, clash.text
    assert clash.json()["reason"] == "vehicle_busy"
    assert clash.json()["appointment_id"] == first["id"]

    # 4 two EV checks at the same time: Chloe is the only ev_hv technician
    ev_first = await client.post("/appointments", json=body(EV, vehicle_id=2, customer_id=1))
    assert ev_first.status_code == 201, ev_first.text
    ev_second = await client.post("/appointments", json=body(EV, vehicle_id=3, customer_id=2))
    assert ev_second.status_code == 409, ev_second.text
    assert ev_second.json()["reason"] == "no_technician"

    # 5 cancel step 1
    cancelled = await client.post(f"/appointments/{first['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # 6 the freed slot can be booked again, on the same bay and technician
    rebooked = await client.post("/appointments", json=body(BRAKES, vehicle_id=1, customer_id=1))
    assert rebooked.status_code == 201, rebooked.text
    again = rebooked.json()
    assert again["id"] != first["id"]
    assert again["bay"] == first["bay"]
    assert again["technician"] == first["technician"]
