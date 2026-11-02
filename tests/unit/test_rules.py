"""Pure booking rules: no database, no HTTP."""

from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.rules import (
    FieldError,
    check_opening_hours,
    check_start,
    compute_times,
    local_day_bounds,
    pick_reason,
)

LONDON = ZoneInfo("Europe/London")
EVERY_DAY_8_TO_18 = {weekday: (time(8), time(18)) for weekday in range(1, 8)}
NOW = datetime(2026, 11, 2, 8, 7, 31, tzinfo=UTC)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# end, reserved_until and the reserved range


def test_compute_times() -> None:
    start = utc(2026, 11, 10, 9, 0)
    end, reserved_until = compute_times(start, duration_min=90, buffer_min=15)
    assert end == utc(2026, 11, 10, 10, 30)
    assert reserved_until == utc(2026, 11, 10, 10, 45)


def test_compute_times_zero_buffer() -> None:
    end, reserved_until = compute_times(utc(2026, 11, 10, 9, 0), duration_min=60, buffer_min=0)
    assert end == reserved_until == utc(2026, 11, 10, 10, 0)


# start validation (clock injected)


def test_start_on_grid_is_valid() -> None:
    assert check_start(utc(2026, 11, 10, 9, 45), NOW) == []


@pytest.mark.parametrize(
    "start",
    [utc(2026, 11, 10, 9, 10), utc(2026, 11, 10, 9, 0, 30), utc(2026, 11, 10, 9, 0, 0, 1)],
    ids=["off-grid", "seconds", "microseconds"],
)
def test_start_off_grid(start: datetime) -> None:
    assert check_start(start, NOW) == [
        FieldError("start", "must be on the 15-minute grid in UTC, with zero seconds")
    ]


def test_grid_is_checked_in_utc() -> None:
    # 09:00 at +05:30 is 03:30 UTC: on the grid
    assert check_start(datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")), NOW) == []
    # 09:10 at +05:45 is 03:25 UTC: off the grid
    assert check_start(datetime(2026, 11, 10, 9, 10, tzinfo=ZoneInfo("Asia/Kathmandu")), NOW) != []


def test_start_in_past() -> None:
    assert check_start(utc(2026, 11, 2, 8, 0), NOW) == [FieldError("start", "must not be in the past")]


def test_start_more_than_90_days_ahead() -> None:
    # NOW + 90 days is 2027-01-31 08:07:31 UTC
    assert check_start(utc(2027, 1, 31, 8, 15), NOW) == [
        FieldError("start", "must be at most 90 days ahead")
    ]
    assert check_start(utc(2027, 1, 31, 8, 0), NOW) == []


def test_off_grid_and_past_report_both() -> None:
    assert check_start(utc(2026, 11, 1, 9, 10), NOW) == [
        FieldError("start", "must be on the 15-minute grid in UTC, with zero seconds"),
        FieldError("start", "must not be in the past"),
    ]


# opening hours (work part only; the buffer may run past closing)


def test_work_inside_hours() -> None:
    # Tuesday 2026-11-10, London is on GMT (UTC+0)
    assert check_opening_hours(utc(2026, 11, 10, 9), utc(2026, 11, 10, 10), LONDON, EVERY_DAY_8_TO_18)


def test_work_ending_exactly_at_closing_is_accepted() -> None:
    # 17:00-18:00 work; its buffer to 18:15 runs past closing, which is allowed
    end, reserved_until = compute_times(utc(2026, 11, 10, 17), 60, 15)
    assert reserved_until > utc(2026, 11, 10, 18)
    assert check_opening_hours(utc(2026, 11, 10, 17), end, LONDON, EVERY_DAY_8_TO_18)


def test_work_past_closing_is_refused() -> None:
    assert not check_opening_hours(
        utc(2026, 11, 10, 17, 15), utc(2026, 11, 10, 18, 15), LONDON, EVERY_DAY_8_TO_18
    )


def test_start_before_opening_is_refused() -> None:
    assert not check_opening_hours(
        utc(2026, 11, 10, 7, 45), utc(2026, 11, 10, 8, 45), LONDON, EVERY_DAY_8_TO_18
    )


def test_closed_weekday_is_refused() -> None:
    weekdays_only = {d: (time(8), time(18)) for d in range(1, 6)}
    saturday = utc(2026, 11, 14, 9)
    assert not check_opening_hours(saturday, saturday + timedelta(hours=1), LONDON, weekdays_only)


def test_work_crossing_local_midnight_is_refused() -> None:
    late = {d: (time(20), time(23, 45)) for d in range(1, 8)}
    assert not check_opening_hours(
        utc(2026, 11, 10, 23, 30), utc(2026, 11, 11, 0, 30), LONDON, late
    )


def test_dst_change_europe_london() -> None:
    # 08:00 London is 07:00 UTC on Sat 2026-10-24 (BST) and 08:00 UTC on Sun 2026-10-25 (GMT)
    sat = utc(2026, 10, 24, 7)
    sun = utc(2026, 10, 25, 7)
    assert check_opening_hours(sat, sat + timedelta(hours=1), LONDON, EVERY_DAY_8_TO_18)
    assert not check_opening_hours(sun, sun + timedelta(hours=1), LONDON, EVERY_DAY_8_TO_18)


# local day for the least-loaded count


def test_local_day_bounds_normal_day() -> None:
    assert local_day_bounds(utc(2026, 11, 10, 9), LONDON) == (
        utc(2026, 11, 10, 0),
        utc(2026, 11, 11, 0),
    )


def test_local_day_bounds_on_dst_change_is_25_hours() -> None:
    start_from, start_to = local_day_bounds(utc(2026, 10, 25, 12), LONDON)
    assert (start_from, start_to) == (utc(2026, 10, 24, 23), utc(2026, 10, 26, 0))
    assert start_to - start_from == timedelta(hours=25)


def test_local_day_bounds_uses_local_date() -> None:
    # 23:30 UTC on 2026-10-24 is 00:30 BST on 2026-10-25, so it belongs to the 25th
    assert local_day_bounds(utc(2026, 10, 24, 23, 30), LONDON)[0] == utc(2026, 10, 24, 23)


# reason when no pair can be booked


@pytest.mark.parametrize(
    ("vehicle_busy", "techs_empty", "reason"),
    [
        (True, True, "vehicle_busy"),
        (True, False, "vehicle_busy"),
        (False, True, "no_technician"),
        (False, False, "no_bay"),
    ],
)
def test_pick_reason(vehicle_busy: bool, techs_empty: bool, reason: str) -> None:
    assert pick_reason(vehicle_busy=vehicle_busy, techs_empty=techs_empty) == reason


@pytest.mark.parametrize(
    "start",
    [
        datetime(1, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=1))),  # UTC would be year 0
        datetime(9999, 12, 31, 23, 0, tzinfo=timezone(timedelta(hours=-2))),  # UTC: year 10000
    ],
    ids=["before-year-1-in-utc", "after-year-9999-in-utc"],
)
def test_start_outside_the_datetime_range_is_a_field_error(start: datetime) -> None:
    assert check_start(start, NOW) == [FieldError("start", "is outside the supported date range")]
