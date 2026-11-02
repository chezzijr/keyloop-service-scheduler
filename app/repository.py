"""All SQL for booking. Candidate queries are advisory; the exclusion constraints decide.

Every function runs on the caller's session and transaction."""

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

EXCLUSION_VIOLATION = "23P01"


@dataclass(frozen=True)
class Overlap:
    """An insert failed on this exclusion constraint (no_vehicle_overlap, no_tech_overlap
    or no_bay_overlap)."""

    constraint: str


@dataclass(frozen=True)
class Dealership:
    id: int
    timezone: str


@dataclass(frozen=True)
class ServiceType:
    id: int
    code: str
    name: str
    duration_minutes: int
    buffer_minutes: int
    required_skill_id: int
    required_skill_code: str
    required_bay_type_id: int


@dataclass(frozen=True)
class AppointmentRecord:
    id: int
    customer_id: int
    vehicle_id: int
    dealership_id: int
    service_type_id: int
    bay_id: int
    bay_name: str
    technician_id: int
    technician_name: str
    start: datetime
    end: datetime
    reserved_until: datetime
    status: str
    created_at: datetime
    cancelled_at: datetime | None


async def get_dealership(session: AsyncSession, dealership_id: int) -> Dealership | None:
    row = (
        await session.execute(
            text("SELECT id, timezone FROM dealership WHERE id = :id"), {"id": dealership_id}
        )
    ).one_or_none()
    return Dealership(*row) if row else None


async def get_service_type(session: AsyncSession, service_type_id: int) -> ServiceType | None:
    row = (
        await session.execute(
            text(
                "SELECT st.id, st.code, st.name, st.duration_minutes, st.buffer_minutes,"
                " st.required_skill_id, s.code, st.required_bay_type_id"
                " FROM service_type st JOIN skill s ON s.id = st.required_skill_id"
                " WHERE st.id = :id"
            ),
            {"id": service_type_id},
        )
    ).one_or_none()
    return ServiceType(*row) if row else None


async def customer_exists(session: AsyncSession, customer_id: int) -> bool:
    return bool(
        await session.scalar(
            text("SELECT EXISTS (SELECT 1 FROM customer WHERE id = :id)"), {"id": customer_id}
        )
    )


async def get_vehicle_owner(session: AsyncSession, vehicle_id: int) -> int | None:
    """The vehicle's customer_id, or None for an unknown vehicle."""
    return await session.scalar(
        text("SELECT customer_id FROM vehicle WHERE id = :id"), {"id": vehicle_id}
    )


async def opening_windows(session: AsyncSession, dealership_id: int) -> dict[int, tuple[time, time]]:
    rows = await session.execute(
        text("SELECT weekday, opens_at, closes_at FROM opening_hours WHERE dealership_id = :id"),
        {"id": dealership_id},
    )
    return {weekday: (opens_at, closes_at) for weekday, opens_at, closes_at in rows}


async def find_vehicle_clash(
    session: AsyncSession, vehicle_id: int, start: datetime, reserved_until: datetime
) -> int | None:
    """Id of an active appointment for this vehicle that overlaps [start, reserved_until)."""
    return await session.scalar(
        text(
            "SELECT id FROM appointment WHERE vehicle_id = :vehicle AND status <> 'cancelled'"
            " AND during && tstzrange(:start, :reserved_until, '[)') ORDER BY id LIMIT 1"
        ),
        {"vehicle": vehicle_id, "start": start, "reserved_until": reserved_until},
    )


async def candidate_techs(
    session: AsyncSession,
    *,
    dealership_id: int,
    skill_id: int,
    start: datetime,
    reserved_until: datetime,
    day_from: datetime,
    day_to: datetime,
) -> list[int]:
    """Qualified technicians with no active appointment overlapping [start, reserved_until),
    least booked work minutes in [day_from, day_to) first, then lowest id (A3, A4).
    Work minutes come from the stored ends_at, not the current service type."""
    rows = await session.scalars(
        text(
            "SELECT t.id FROM technician t"
            " JOIN technician_skill ts ON ts.technician_id = t.id AND ts.skill_id = :skill"
            " WHERE t.dealership_id = :dealership"
            " AND NOT EXISTS (SELECT 1 FROM appointment a WHERE a.technician_id = t.id"
            "   AND a.status <> 'cancelled'"
            "   AND a.during && tstzrange(:start, :reserved_until, '[)'))"
            " ORDER BY (SELECT coalesce(sum(a.ends_at - lower(a.during)), interval '0')"
            "   FROM appointment a WHERE a.technician_id = t.id AND a.status <> 'cancelled'"
            "   AND lower(a.during) >= :day_from AND lower(a.during) < :day_to), t.id"
        ),
        {
            "skill": skill_id,
            "dealership": dealership_id,
            "start": start,
            "reserved_until": reserved_until,
            "day_from": day_from,
            "day_to": day_to,
        },
    )
    return list(rows)


async def candidate_bays(
    session: AsyncSession,
    *,
    dealership_id: int,
    bay_type_id: int,
    start: datetime,
    reserved_until: datetime,
) -> list[int]:
    """Compatible bays with no active appointment overlapping [start, reserved_until), by id (A5)."""
    rows = await session.scalars(
        text(
            "SELECT b.id FROM service_bay b"
            " WHERE b.dealership_id = :dealership AND b.bay_type_id = :bay_type"
            " AND NOT EXISTS (SELECT 1 FROM appointment a WHERE a.service_bay_id = b.id"
            "   AND a.status <> 'cancelled'"
            "   AND a.during && tstzrange(:start, :reserved_until, '[)'))"
            " ORDER BY b.id"
        ),
        {
            "dealership": dealership_id,
            "bay_type": bay_type_id,
            "start": start,
            "reserved_until": reserved_until,
        },
    )
    return list(rows)


async def insert_appointment(
    session: AsyncSession,
    *,
    customer_id: int,
    vehicle_id: int,
    dealership_id: int,
    service_type_id: int,
    technician_id: int,
    bay_id: int,
    start: datetime,
    end: datetime,
    reserved_until: datetime,
) -> int | Overlap:
    """Insert under a SAVEPOINT. Returns the new id, or the exclusion constraint that
    rejected it; on a clash only the savepoint is rolled back, so the transaction goes on."""
    try:
        async with session.begin_nested():
            return (
                await session.execute(
                    text(
                        "INSERT INTO appointment (customer_id, vehicle_id, dealership_id,"
                        " service_type_id, service_bay_id, technician_id, during, ends_at)"
                        " VALUES (:customer, :vehicle, :dealership, :service_type, :bay, :tech,"
                        " tstzrange(:start, :reserved_until, '[)'), :end) RETURNING id"
                    ),
                    {
                        "customer": customer_id,
                        "vehicle": vehicle_id,
                        "dealership": dealership_id,
                        "service_type": service_type_id,
                        "bay": bay_id,
                        "tech": technician_id,
                        "start": start,
                        "reserved_until": reserved_until,
                        "end": end,
                    },
                )
            ).scalar_one()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != EXCLUSION_VIOLATION:
            raise
        return Overlap(exc.orig.__cause__.constraint_name)


SELECT_RECORD = (
    "SELECT a.id, a.customer_id, a.vehicle_id, a.dealership_id, a.service_type_id,"
    " b.id, b.name, t.id, t.name, lower(a.during), a.ends_at, upper(a.during),"
    " a.status, a.created_at, a.cancelled_at"
    " FROM appointment a"
    " JOIN service_bay b ON b.id = a.service_bay_id"
    " JOIN technician t ON t.id = a.technician_id"
)


async def get_appointment(session: AsyncSession, appointment_id: int) -> AppointmentRecord | None:
    row = (
        await session.execute(text(SELECT_RECORD + " WHERE a.id = :id"), {"id": appointment_id})
    ).one_or_none()
    return AppointmentRecord(*row) if row else None


async def cancel_appointment(session: AsyncSession, appointment_id: int) -> None:
    """Set cancelled once; a second call changes nothing (A20)."""
    await session.execute(
        text(
            "UPDATE appointment SET status = 'cancelled', cancelled_at = now()"
            " WHERE id = :id AND status = 'confirmed'"
        ),
        {"id": appointment_id},
    )
