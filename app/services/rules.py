"""Booking rules as plain functions: no database and no HTTP types, so they are unit tested.

Assumption numbers refer to docs/details/assumptions.md.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

GRID_MINUTES = 15
BOOKING_WINDOW = timedelta(days=90)

# ISO weekday (1 = Monday) -> (opens_at, closes_at), local wall-clock times
OpeningWindows = Mapping[int, tuple[time, time]]


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


def compute_times(start: datetime, duration_min: int, buffer_min: int) -> tuple[datetime, datetime]:
    """Planned end of work and end of the reserved range [start, reserved_until) (A9, A24)."""
    end = start + timedelta(minutes=duration_min)
    return end, end + timedelta(minutes=buffer_min)


def check_start(start: datetime, now: datetime) -> list[FieldError]:
    """15-minute grid in UTC with zero seconds (A8); not in the past, at most 90 days ahead (A14)."""
    try:
        start_utc = start.astimezone(UTC)
    except OverflowError:  # e.g. 0001-01-01T00:00+01:00 is before year 1 in UTC
        return [FieldError("start", "is outside the supported date range")]
    errors = []
    if start_utc.minute % GRID_MINUTES or start_utc.second or start_utc.microsecond:
        errors.append(
            FieldError("start", "must be on the 15-minute grid in UTC, with zero seconds")
        )
    if start < now:
        errors.append(FieldError("start", "must not be in the past"))
    elif start > now + BOOKING_WINDOW:
        errors.append(FieldError("start", "must be at most 90 days ahead"))
    return errors


def check_opening_hours(
    start: datetime, end: datetime, tz: ZoneInfo, windows: OpeningWindows
) -> bool:
    """The work part [start, end) lies inside that weekday's local window and does not cross
    local midnight (A12, A13). The buffer is not checked: it may run past closing."""
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)
    if local_end.date() != local_start.date():
        return False
    window = windows.get(local_start.isoweekday())
    if window is None:
        return False
    opens_at, closes_at = window
    return opens_at <= local_start.time() and local_end.time() <= closes_at


def local_day_bounds(start: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Local midnight to the next local midnight of start's local date, in UTC. On a DST
    change day this span is 23 or 25 hours. Used for the least-loaded count (A3, A4)."""
    day = start.astimezone(tz).date()
    day_start = datetime.combine(day, time(0), tzinfo=tz)
    next_day_start = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return day_start.astimezone(UTC), next_day_start.astimezone(UTC)


def pick_reason(*, vehicle_busy: bool, techs_empty: bool) -> str:
    """Reason when no (technician, bay) pair could be booked. Order: vehicle_busy, then
    no_technician, then no_bay (design.md Data Flow)."""
    if vehicle_busy:
        return "vehicle_busy"
    if techs_empty:
        return "no_technician"
    return "no_bay"
