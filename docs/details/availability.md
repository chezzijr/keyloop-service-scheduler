# Availability Algorithm

Part of the [system design](../design.md). Assumption numbers refer to [assumptions.md](assumptions.md).

Input: `dealership_id`, `service_type_id`, `date` (local date in the dealership's time zone).

Steps:

1. Load the service type: duration, required bay type, required skill.
2. Find the opening window for the date's ISO weekday. No row for that weekday means the dealership is closed and the result is an empty list.
3. Convert the local window to UTC with the dealership's IANA time zone using Python `zoneinfo`. The conversion is done for that exact date, so a DST change (for example the last Sunday of October in `Europe/London`) gives the right UTC times. Local midnight to the next local midnight gives the UTC range of the day.
4. Generate candidate starts on the 15-minute UTC grid (Assumption 8) where `start >= opens_at` and `start + duration <= closes_at` (the buffer may run past closing, Assumption 13). Drop starts in the past or more than 90 days ahead (Assumption 14).
5. In one query, load the active appointments that overlap the day for the dealership's compatible bays and qualified technicians. In the same round trip, load the shifts of those technicians for that weekday.
6. In memory, a start `s` is free if both hold:
   - some qualified technician has a shift that covers `[s, s + duration)` and no active appointment that overlaps `[s, s + duration + buffer)`, and
   - some compatible bay has no active appointment that overlaps `[s, s + duration + buffer)`.
7. Return the free starts in time order.

Cost. One or two queries per request, then `O(slots x (techs + bays))` checks in memory, where each check scans that resource's appointments for the day. A 10-hour day has 40 slots. With 20 resources and a handful of appointments each, that is a few thousand comparisons. This is small enough that no cache is needed.
