"""Book, read and cancel appointments. Follows the Data Flow in docs/design.md.

Returns records and raises typed errors; no HTTP types here (the routers map them)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.repository import AppointmentRecord, Overlap
from app.services.rules import (
    FieldError,
    check_opening_hours,
    check_start,
    compute_times,
    local_day_bounds,
    pick_reason,
)

__all__ = [
    "AppointmentNotFound",
    "AppointmentRecord",
    "BookingConflict",
    "BookingRequest",
    "BookingValidationError",
    "book",
    "cancel",
    "get",
]


@dataclass(frozen=True)
class BookingRequest:
    customer_id: int
    vehicle_id: int
    dealership_id: int
    service_type_id: int
    start: datetime


class BookingValidationError(Exception):
    """The request can never succeed as sent (422). reason is "outside_hours" or None."""

    def __init__(self, errors: list[FieldError], reason: str | None = None) -> None:
        super().__init__(errors)
        self.errors = errors
        self.reason = reason


class BookingConflict(Exception):
    """Valid request, but other bookings are in the way (409)."""

    def __init__(self, reason: str, detail: str, appointment_id: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.appointment_id = appointment_id


class AppointmentNotFound(Exception):
    pass


def _vehicle_busy(appointment_id: int) -> BookingConflict:
    return BookingConflict(
        "vehicle_busy",
        f"The vehicle already has appointment {appointment_id} at this time.",
        appointment_id,
    )


async def book(session: AsyncSession, req: BookingRequest, *, now: datetime) -> AppointmentRecord:
    async with session.begin():
        errors = check_start(req.start, now)
        dealership = await repository.get_dealership(session, req.dealership_id)
        service_type = await repository.get_service_type(session, req.service_type_id)
        owner = await repository.get_vehicle_owner(session, req.vehicle_id)
        if dealership is None:
            errors.append(FieldError("dealership_id", "unknown dealership"))
        if service_type is None:
            errors.append(FieldError("service_type_id", "unknown service type"))
        if not await repository.customer_exists(session, req.customer_id):
            errors.append(FieldError("customer_id", "unknown customer"))
        if owner is None:
            errors.append(FieldError("vehicle_id", "unknown vehicle"))
        elif owner != req.customer_id:
            errors.append(FieldError("vehicle_id", "does not belong to the customer"))
        if errors:
            raise BookingValidationError(errors)
        assert dealership is not None and service_type is not None

        start = req.start
        end, reserved_until = compute_times(
            start, service_type.duration_minutes, service_type.buffer_minutes
        )
        tz = ZoneInfo(dealership.timezone)
        windows = await repository.opening_windows(session, dealership.id)
        if not check_opening_hours(start, end, tz, windows):
            raise BookingValidationError(
                [FieldError("start", "the work must fit inside the dealership's opening hours")],
                reason="outside_hours",
            )

        clash = await repository.find_vehicle_clash(session, req.vehicle_id, start, reserved_until)
        if clash is not None:
            raise _vehicle_busy(clash)

        day_from, day_to = local_day_bounds(start, tz)
        techs = await repository.candidate_techs(
            session,
            dealership_id=dealership.id,
            skill_id=service_type.required_skill_id,
            start=start,
            reserved_until=reserved_until,
            day_from=day_from,
            day_to=day_to,
        )
        bays = await repository.candidate_bays(
            session,
            dealership_id=dealership.id,
            bay_type_id=service_type.required_bay_type_id,
            start=start,
            reserved_until=reserved_until,
        )

        # Each failed attempt drops one technician or one bay, so the loop ends after at
        # most len(techs) + len(bays) attempts; no fixed cap (design.md Data Flow).
        while techs and bays:
            outcome = await repository.insert_appointment(
                session,
                customer_id=req.customer_id,
                vehicle_id=req.vehicle_id,
                dealership_id=dealership.id,
                service_type_id=service_type.id,
                technician_id=techs[0],
                bay_id=bays[0],
                start=start,
                end=end,
                reserved_until=reserved_until,
            )
            if not isinstance(outcome, Overlap):
                record = await repository.get_appointment(session, outcome)
                assert record is not None
                return record
            if outcome.constraint == "no_tech_overlap":
                techs.pop(0)
            elif outcome.constraint == "no_bay_overlap":
                bays.pop(0)
            else:  # no_vehicle_overlap: another pair cannot help
                break

        # A resent request may lose on any index; read the vehicle again so the reason
        # does not depend on index order.
        clash = await repository.find_vehicle_clash(session, req.vehicle_id, start, reserved_until)
        reason = pick_reason(vehicle_busy=clash is not None, techs_empty=not techs)
        if clash is not None:  # reason == "vehicle_busy"
            raise _vehicle_busy(clash)
        start_utc, until_utc = start.astimezone(UTC), reserved_until.astimezone(UTC)
        window = f"{start_utc:%H:%M} to {until_utc:%H:%M} UTC on {start_utc:%Y-%m-%d}"
        work = (
            f"{service_type.name}: {service_type.duration_minutes} min work"
            f" + {service_type.buffer_minutes} min buffer"
        )
        if reason == "no_technician":
            detail = (
                f"No technician with skill {service_type.required_skill_code}"
                f" is free from {window} ({work})."
            )
        else:
            detail = f"No compatible bay is free from {window} ({work})."
        raise BookingConflict(reason, detail)


async def get(session: AsyncSession, appointment_id: int) -> AppointmentRecord:
    async with session.begin():
        record = await repository.get_appointment(session, appointment_id)
    if record is None:
        raise AppointmentNotFound(appointment_id)
    return record


async def cancel(session: AsyncSession, appointment_id: int) -> AppointmentRecord:
    async with session.begin():
        await repository.cancel_appointment(session, appointment_id)
        record = await repository.get_appointment(session, appointment_id)
    if record is None:
        raise AppointmentNotFound(appointment_id)
    return record
