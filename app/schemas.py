"""Request and response bodies (docs/details/api.md)."""

from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field

from app.repository import AppointmentRecord

# ids are Postgres bigint identities: a value outside 1..2^63-1 can never exist, and
# asyncpg cannot even send one, so it is a 422 here instead of a database error
ID_MAX = 2**63 - 1
Id = Annotated[int, Field(gt=0, le=ID_MAX)]


class BookingIn(BaseModel):
    customer_id: Id
    vehicle_id: Id
    dealership_id: Id
    service_type_id: Id
    start: AwareDatetime  # a start without an offset is a 422, not a guess


def example_booking(today: datetime | None = None) -> dict[str, Any]:
    """A booking on the seed data for the /docs page: brake pads for vehicle 1, on the first
    weekday at least 7 days ahead, 10:00 London time. Computed when the app starts, so
    "Try it out" works for a week of uptime and stays inside the 90-day window."""
    london = ZoneInfo("Europe/London")
    day = (today or datetime.now(UTC)).astimezone(london).date() + timedelta(days=7)
    while day.isoweekday() > 5:
        day += timedelta(days=1)
    start = datetime.combine(day, time(10), tzinfo=london).astimezone(UTC)
    return {
        "customer_id": 1,
        "vehicle_id": 1,
        "dealership_id": 1,
        "service_type_id": 3,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class FieldErrorOut(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """RFC 9457 problem body, as sent by app/errors.py."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str | None = None
    reason: str | None = None  # 409, and 422 outside_hours
    appointment_id: int | None = None  # 409 vehicle_busy
    errors: list[FieldErrorOut] | None = None  # 422


def problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries for error codes, documented with the problem+json media type."""
    descriptions = {
        404: "Appointment not found",
        409: "Conflicts with other bookings: reason no_bay, no_technician or vehicle_busy",
        422: "Invalid request; failing fields in errors",
        503: "Database unreachable or too slow",
    }
    ref = {"$ref": "#/components/schemas/Problem"}
    return {
        code: {
            "model": Problem,
            "description": descriptions[code],
            "content": {"application/problem+json": {"schema": ref}},
        }
        for code in codes
    }


class Ref(BaseModel):
    id: int
    name: str


class AppointmentOut(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    dealership_id: int
    service_type_id: int
    bay: Ref
    technician: Ref
    start: datetime
    end: datetime
    reserved_until: datetime
    status: str
    created_at: datetime

    @classmethod
    def from_record(cls, r: AppointmentRecord) -> "AppointmentOut":
        return cls(
            id=r.id,
            customer_id=r.customer_id,
            vehicle_id=r.vehicle_id,
            dealership_id=r.dealership_id,
            service_type_id=r.service_type_id,
            bay=Ref(id=r.bay_id, name=r.bay_name),
            technician=Ref(id=r.technician_id, name=r.technician_name),
            start=r.start,
            end=r.end,
            reserved_until=r.reserved_until,
            status=r.status,
            created_at=r.created_at,
        )
