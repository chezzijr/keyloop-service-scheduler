"""Database guards from docs/details/data-model.md and design.md, checked with direct SQL.

Each test starts on empty tables with identities reset, so rows inserted without an id
get 1, 2, ... in insert order."""

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def make_refs(conn: AsyncConnection) -> None:
    """Dealership 1 has bays 1, 2 and technicians 1, 2; dealership 2 has bay 3 and
    technician 3. Customer 1 owns vehicles 1 and 2. Service type 1 (60 + 15 min)."""
    await conn.execute(
        text(
            "INSERT INTO dealership (name, timezone) VALUES"
            " ('North', 'Europe/London'), ('South', 'Europe/London')"
        )
    )
    await conn.execute(text("INSERT INTO bay_type (code) VALUES ('lift')"))
    await conn.execute(text("INSERT INTO skill (code) VALUES ('general')"))
    await conn.execute(
        text(
            "INSERT INTO service_bay (dealership_id, bay_type_id, name) VALUES"
            " (1, 1, 'Bay 1'), (1, 1, 'Bay 2'), (2, 1, 'Bay 3')"
        )
    )
    await conn.execute(
        text("INSERT INTO technician (dealership_id, name) VALUES (1, 'A'), (1, 'B'), (2, 'C')")
    )
    await conn.execute(
        text(
            "INSERT INTO service_type (code, name, duration_minutes, buffer_minutes,"
            " required_skill_id, required_bay_type_id) VALUES ('oil', 'Oil', 60, 15, 1, 1)"
        )
    )
    await conn.execute(text("INSERT INTO customer (name, email) VALUES ('Ann', 'ann@example.com')"))
    await conn.execute(
        text(
            "INSERT INTO vehicle (customer_id, vin, make, model) VALUES"
            " (1, 'VIN00000000000001', 'Ford', 'Focus'),"
            " (1, 'VIN00000000000002', 'Kia', 'Niro')"
        )
    )


@pytest.fixture
async def refs(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await make_refs(conn)


async def insert_appt(
    engine: AsyncEngine,
    *,
    bay: int = 1,
    tech: int = 1,
    vehicle: int = 1,
    start: int = 0,
    reserved: int = 75,
    ends: int | None = None,
    status: str = "confirmed",
    dealership: int = 1,
) -> None:
    """Insert one appointment; start, reserved and ends are minutes after T0."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO appointment (customer_id, vehicle_id, dealership_id,"
                " service_type_id, service_bay_id, technician_id, during, ends_at, status)"
                " VALUES (1, :vehicle, :dealership, 1, :bay, :tech,"
                " tstzrange(:start, :reserved, '[)'), :ends, :status)"
            ),
            {
                "vehicle": vehicle,
                "dealership": dealership,
                "bay": bay,
                "tech": tech,
                "start": at(start),
                "reserved": at(reserved),
                "ends": at(reserved - 15 if ends is None else ends),
                "status": status,
            },
        )


def violation(exc: pytest.ExceptionInfo[IntegrityError]) -> tuple[str, str]:
    """(sqlstate, constraint_name) of the asyncpg error behind a SQLAlchemy IntegrityError."""
    orig = exc.value.orig
    return orig.sqlstate, orig.__cause__.constraint_name


async def expect_violation(engine: AsyncEngine, sqlstate: str, name: str, **kwargs: object) -> None:
    with pytest.raises(IntegrityError) as exc:
        await insert_appt(engine, **kwargs)
    assert violation(exc) == (sqlstate, name)


@pytest.mark.usefixtures("refs")
async def test_bay_overlap_rejected(engine: AsyncEngine) -> None:
    await insert_appt(engine, bay=1, tech=1, vehicle=1)
    await expect_violation(
        engine, "23P01", "no_bay_overlap", bay=1, tech=2, vehicle=2, start=15, reserved=90
    )


@pytest.mark.usefixtures("refs")
async def test_tech_overlap_rejected(engine: AsyncEngine) -> None:
    await insert_appt(engine, bay=1, tech=1, vehicle=1)
    await expect_violation(
        engine, "23P01", "no_tech_overlap", bay=2, tech=1, vehicle=2, start=15, reserved=90
    )


@pytest.mark.usefixtures("refs")
async def test_vehicle_overlap_rejected(engine: AsyncEngine) -> None:
    await insert_appt(engine, bay=1, tech=1, vehicle=1)
    await expect_violation(
        engine, "23P01", "no_vehicle_overlap", bay=2, tech=2, vehicle=1, start=15, reserved=90
    )


@pytest.mark.usefixtures("refs")
async def test_resent_request_fails_on_vehicle_first(engine: AsyncEngine) -> None:
    # the same bay, tech and vehicle clash; no_vehicle_overlap is created first, so it is named
    await insert_appt(engine, bay=1, tech=1, vehicle=1)
    await expect_violation(engine, "23P01", "no_vehicle_overlap", bay=1, tech=1, vehicle=1)


@pytest.mark.usefixtures("refs")
async def test_touching_ranges_allowed(engine: AsyncEngine) -> None:
    await insert_appt(engine, start=0, reserved=75)
    await insert_appt(engine, start=75, reserved=150)


@pytest.mark.usefixtures("refs")
async def test_cancelled_row_does_not_block(engine: AsyncEngine) -> None:
    await insert_appt(engine, status="cancelled")
    await insert_appt(engine, status="confirmed")


@pytest.mark.usefixtures("refs")
async def test_status_valid(engine: AsyncEngine) -> None:
    await expect_violation(engine, "23514", "status_valid", status="canceled")


@pytest.mark.usefixtures("refs")
async def test_bay_in_dealership(engine: AsyncEngine) -> None:
    await expect_violation(engine, "23503", "bay_in_dealership", bay=3, tech=1)


@pytest.mark.usefixtures("refs")
async def test_tech_in_dealership(engine: AsyncEngine) -> None:
    await expect_violation(engine, "23503", "tech_in_dealership", bay=1, tech=3)


@pytest.mark.usefixtures("refs")
@pytest.mark.parametrize("ends", [0, -15, 90])
async def test_ends_at_in_during(engine: AsyncEngine, ends: int) -> None:
    await expect_violation(engine, "23514", "ends_at_in_during", start=0, reserved=75, ends=ends)


@pytest.mark.usefixtures("refs")
async def test_ends_at_may_equal_reserved_end(engine: AsyncEngine) -> None:
    await insert_appt(engine, start=0, reserved=60, ends=60)  # buffer 0


@pytest.mark.usefixtures("refs")
@pytest.mark.parametrize(
    ("duration", "buffer", "name"),
    [(0, 15, "duration_valid"), (20, 15, "duration_valid"), (60, 10, "buffer_valid")],
)
async def test_service_type_checks(
    engine: AsyncEngine, duration: int, buffer: int, name: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO service_type (code, name, duration_minutes, buffer_minutes,"
                    " required_skill_id, required_bay_type_id) VALUES ('x', 'X', :d, :b, 1, 1)"
                ),
                {"d": duration, "b": buffer},
            )
    assert violation(exc) == ("23514", name)


@pytest.mark.usefixtures("refs")
@pytest.mark.parametrize(
    ("weekday", "opens", "closes", "name"),
    [
        (8, time(8), time(18), "weekday_valid"),
        (0, time(8), time(18), "weekday_valid"),
        (1, time(18), time(18), "hours_valid"),
        (1, time(18), time(8), "hours_valid"),
    ],
)
async def test_opening_hours_checks(
    engine: AsyncEngine, weekday: int, opens: time, closes: time, name: str
) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO opening_hours (dealership_id, weekday, opens_at, closes_at)"
                    " VALUES (1, :w, :o, :c)"
                ),
                {"w": weekday, "o": opens, "c": closes},
            )
    assert violation(exc) == ("23514", name)


async def test_seed_then_normal_inserts(engine: AsyncEngine) -> None:
    from scripts.seed import seed

    async with engine.begin() as conn:
        await seed(conn)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO dealership (name, timezone) VALUES ('New', 'Europe/London')")
        )
        await conn.execute(
            text(
                "INSERT INTO vehicle (customer_id, vin, make, model)"
                " VALUES (1, 'NEWVIN00000000001', 'VW', 'Golf')"
            )
        )
    await insert_appt(engine, bay=1, tech=1, vehicle=1)


async def test_seed_refuses_non_empty_database(engine: AsyncEngine) -> None:
    from scripts.seed import seed

    async with engine.begin() as conn:
        await seed(conn)
    with pytest.raises(RuntimeError, match="not empty"):
        async with engine.begin() as conn:
            await seed(conn)
