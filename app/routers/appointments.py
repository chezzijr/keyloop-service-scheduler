"""Book, read and cancel. Service errors become problem+json in app/errors.py."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import ID_MAX, AppointmentOut, BookingIn, example_booking, problem_responses
from app.services import booking

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]
AppointmentId = Annotated[int, Path(gt=0, le=ID_MAX)]


def get_now() -> datetime:
    """The clock, as a dependency so tests can fix it."""
    return datetime.now(UTC)


BOOKING_EXAMPLES = {
    "brake_pads": {"summary": "Brake pads for vehicle 1 (seed data)", "value": example_booking()}
}


@router.post("/appointments", status_code=201, responses=problem_responses(409, 422, 503))
async def book(
    body: Annotated[BookingIn, Body(openapi_examples=BOOKING_EXAMPLES)],
    session: Session,
    now: Annotated[datetime, Depends(get_now)],
) -> AppointmentOut:
    record = await booking.book(session, booking.BookingRequest(**body.model_dump()), now=now)
    return AppointmentOut.from_record(record)


@router.get("/appointments/{appointment_id}", responses=problem_responses(404, 422, 503))
async def get(appointment_id: AppointmentId, session: Session) -> AppointmentOut:
    return AppointmentOut.from_record(await booking.get(session, appointment_id))


@router.post("/appointments/{appointment_id}/cancel", responses=problem_responses(404, 422, 503))
async def cancel(appointment_id: AppointmentId, session: Session) -> AppointmentOut:
    return AppointmentOut.from_record(await booking.cancel(session, appointment_id))
