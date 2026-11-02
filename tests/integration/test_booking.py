"""Booking service and repository on real Postgres, called directly (no HTTP).

Uses the seed data (ids in scripts/seed.py). Tuesday 2026-11-10 is on GMT, so UTC
equals London local time; the dealership is open 08:00-18:00 on weekdays."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import repository
from app.services import booking
from app.services.booking import (
    AppointmentNotFound,
    BookingConflict,
    BookingRequest,
    BookingValidationError,
)
from app.services.rules import FieldError
from scripts.seed import seed

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
# seed ids
OIL, ANNUAL, BRAKES, ALIGNMENT, MOT, EV = 1, 2, 3, 4, 5, 6
ALICE, BEN, CHLOE, DAN = 1, 2, 3, 4
LIFT_1, LIFT_2, ALIGNMENT_BAY, MOT_LANE = 1, 2, 4, 5


def tue(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=UTC)


@pytest.fixture
async def seeded(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await seed(conn)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s


async def book(
    session: AsyncSession,
    start: datetime,
    service_type_id: int = OIL,
    vehicle_id: int = 1,
    customer_id: int = 1,
    dealership_id: int = 1,
) -> booking.AppointmentRecord:
    request = BookingRequest(
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        dealership_id=dealership_id,
        service_type_id=service_type_id,
        start=start,
    )
    return await booking.book(session, request, now=NOW)


async def conflict(session: AsyncSession, *args: object, **kwargs: object) -> BookingConflict:
    with pytest.raises(BookingConflict) as exc:
        await book(session, *args, **kwargs)
    return exc.value


async def only_alice_does_general_work(session: AsyncSession) -> None:
    async with session.begin():
        await session.execute(
            text("DELETE FROM technician_skill WHERE skill_id = 1 AND technician_id <> 1")
        )


# booked record


async def test_book_returns_the_stored_record(seeded: AsyncSession) -> None:
    record = await book(seeded, tue(9), BRAKES)
    assert record.status == "confirmed"
    assert (record.start, record.end, record.reserved_until) == (tue(9), tue(10, 30), tue(10, 45))
    assert (record.technician_id, record.technician_name) == (ALICE, "Alice")
    assert (record.bay_id, record.bay_name) == (LIFT_1, "Lift 1")
    assert await booking.get(seeded, record.id) == record


# overlaps


async def test_bay_overlap_gives_no_bay(seeded: AsyncSession) -> None:
    # two alignment technicians (Alice, Dan) but one alignment bay
    await book(seeded, tue(9), ALIGNMENT, vehicle_id=1)
    assert (await conflict(seeded, tue(9), ALIGNMENT, vehicle_id=3, customer_id=2)).reason == "no_bay"


async def test_tech_overlap_gives_no_technician(seeded: AsyncSession) -> None:
    # one ev_hv technician (Chloe), three lifts
    await book(seeded, tue(9), EV, vehicle_id=2)
    error = await conflict(seeded, tue(9, 30), EV, vehicle_id=3, customer_id=2)
    assert error.reason == "no_technician"


async def test_touching_reserved_ranges_both_succeed(seeded: AsyncSession) -> None:
    # MOT: one technician and one lane; 10:00 + 60 + 15 is reserved until 11:15
    first = await book(seeded, tue(10), MOT, vehicle_id=1)
    second = await book(seeded, tue(11, 15), MOT, vehicle_id=3, customer_id=2)
    assert first.reserved_until == second.start
    assert (first.technician_id, first.bay_id) == (second.technician_id, second.bay_id)


async def test_buffer_blocks_the_technician(seeded: AsyncSession) -> None:
    await only_alice_does_general_work(seeded)
    first = await book(seeded, tue(9), BRAKES, vehicle_id=1)  # reserved until 10:45
    assert first.reserved_until == tue(10, 45)
    error = await conflict(seeded, tue(10, 30), OIL, vehicle_id=3, customer_id=2)
    assert error.reason == "no_technician"
    assert (await book(seeded, tue(10, 45), OIL, vehicle_id=3, customer_id=2)).technician_id == ALICE


async def test_vehicle_busy_names_the_existing_appointment(seeded: AsyncSession) -> None:
    first = await book(seeded, tue(9), OIL, vehicle_id=1)
    error = await conflict(seeded, tue(9, 30), OIL, vehicle_id=1)  # other techs and lifts free
    assert (error.reason, error.appointment_id) == ("vehicle_busy", first.id)


# cancel and read


async def test_cancel_frees_the_slot(seeded: AsyncSession) -> None:
    first = await book(seeded, tue(9), MOT, vehicle_id=1)
    cancelled = await booking.cancel(seeded, first.id)
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None
    again = await book(seeded, tue(9), MOT, vehicle_id=3, customer_id=2)
    assert (again.technician_id, again.bay_id) == (first.technician_id, first.bay_id)


async def test_cancel_twice_returns_the_same_record(seeded: AsyncSession) -> None:
    record = await book(seeded, tue(9))
    first = await booking.cancel(seeded, record.id)
    assert await booking.cancel(seeded, record.id) == first


async def test_unknown_id_is_not_found(seeded: AsyncSession) -> None:
    with pytest.raises(AppointmentNotFound):
        await booking.get(seeded, 999)
    with pytest.raises(AppointmentNotFound):
        await booking.cancel(seeded, 999)


# technician choice


async def test_least_loaded_technician_that_local_day(seeded: AsyncSession) -> None:
    # alignment techs are Alice (1) and Dan (4); equal load -> lowest id
    monday = await book(seeded, tue(9) - timedelta(days=1), ALIGNMENT, vehicle_id=1)
    assert monday.technician_id == ALICE
    # Monday's work does not count on Tuesday, so the tie goes to Alice again
    first = await book(seeded, tue(9), ALIGNMENT, vehicle_id=1)
    assert first.technician_id == ALICE
    # Alice has 60 booked minutes on Tuesday, Dan none
    second = await book(seeded, tue(14), ALIGNMENT, vehicle_id=3, customer_id=2)
    assert second.technician_id == DAN


async def test_cancelled_work_does_not_count_as_load(seeded: AsyncSession) -> None:
    first = await book(seeded, tue(9), ALIGNMENT, vehicle_id=1)
    await booking.cancel(seeded, first.id)
    assert (await book(seeded, tue(14), ALIGNMENT, vehicle_id=3, customer_id=2)).technician_id == ALICE


# validation


@pytest.mark.parametrize(
    "start",
    [tue(7, 45), tue(17, 30), datetime(2026, 11, 8, 9, tzinfo=UTC)],
    ids=["before-opening", "work-past-closing", "sunday-closed"],
)
async def test_outside_hours(seeded: AsyncSession, start: datetime) -> None:
    with pytest.raises(BookingValidationError) as exc:
        await book(seeded, start, OIL)  # 45 min: 17:30 ends 18:15
    assert exc.value.reason == "outside_hours"


async def test_work_ending_at_closing_is_booked(seeded: AsyncSession) -> None:
    record = await book(seeded, tue(17), MOT)  # work to 18:00, buffer to 18:15
    assert record.end == tue(18)


async def test_vehicle_of_another_customer(seeded: AsyncSession) -> None:
    with pytest.raises(BookingValidationError) as exc:
        await book(seeded, tue(9), vehicle_id=1, customer_id=2)
    assert exc.value.errors == [FieldError("vehicle_id", "does not belong to the customer")]
    assert exc.value.reason is None


async def test_unknown_reference_ids_and_bad_start_are_all_reported(seeded: AsyncSession) -> None:
    with pytest.raises(BookingValidationError) as exc:
        await book(seeded, tue(9, 10), service_type_id=99, dealership_id=99)
    assert {e.field for e in exc.value.errors} == {"start", "dealership_id", "service_type_id"}


# stored work end


async def test_ends_at_unchanged_after_service_type_edit(seeded: AsyncSession) -> None:
    record = await book(seeded, tue(9), ALIGNMENT)
    async with seeded.begin():
        await seeded.execute(text("UPDATE service_type SET duration_minutes = 120 WHERE id = 4"))
    stored = await booking.get(seeded, record.id)
    assert (stored.end, stored.reserved_until) == (tue(10), tue(10, 15))


# insert loop: a clash the pre-filter did not see


async def test_insert_reports_the_clashing_constraint(seeded: AsyncSession) -> None:
    taken = await book(seeded, tue(9), EV, vehicle_id=2)  # Chloe in Lift 1
    async with seeded.begin():
        outcome = await repository.insert_appointment(
            seeded,
            customer_id=2,
            vehicle_id=3,
            dealership_id=1,
            service_type_id=EV,
            technician_id=taken.technician_id,
            bay_id=LIFT_2,
            start=tue(9),
            end=tue(10),
            reserved_until=tue(10, 15),
        )
        assert outcome == repository.Overlap("no_tech_overlap")
        # the savepoint rollback keeps the transaction usable
        assert (await seeded.execute(text("SELECT count(*) FROM appointment"))).scalar_one() == 1


async def test_loop_drops_the_clashing_technician(
    seeded: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = await book(seeded, tue(9), ALIGNMENT, vehicle_id=1)  # Alice in the alignment bay
    await booking.cancel(seeded, taken.id)  # frees the bay, then Alice gets other work
    await book(seeded, tue(9), OIL, vehicle_id=1)  # Alice in Lift 1

    async def stale_candidates(*args: object, **kwargs: object) -> list[int]:
        return [ALICE, DAN]  # as if read before Alice was booked

    monkeypatch.setattr(repository, "candidate_techs", stale_candidates)
    record = await book(seeded, tue(9), ALIGNMENT, vehicle_id=3, customer_id=2)
    assert (record.technician_id, record.bay_id) == (DAN, ALIGNMENT_BAY)
