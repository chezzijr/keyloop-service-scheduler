# Testing Strategy: test cases

Part of the [system design](../design.md).

Unit tests (no database, fast):

- End time: `end` = start + `duration_minutes`, `reserved_until` = `end` + `buffer_minutes`, and the half-open range `[start, reserved_until)`.
- Buffer near closing: a job whose work ends exactly at closing time is accepted even though its buffer runs past it.
- Request validation: a start off the 15-minute grid, with seconds, in the past, or more than 90 days ahead is rejected with 422. The clock is injected, so tests do not depend on today's date.
- DST: the opening-hours check in `Europe/London` around 2026-10-25 (a Sunday), the day clocks go back. The test passes its own opening windows (08:00 local), so it does not depend on seed data. 08:00 local is 07:00 UTC on 2026-10-24 and 08:00 UTC on 2026-10-25, so a 07:00 UTC start is accepted on the first day and refused with `outside_hours` on the second. The clock is fixed to a date before them, so the past-start rule does not interfere.
- Reason precedence: a vehicle booked meanwhile gives `vehicle_busy` first. Otherwise an empty tech list gives `no_technician` even when bays are also empty. Empty bay list only gives `no_bay`. 

Integration tests on real Postgres (testcontainers starts `postgres:18`; Alembic builds the schema; each test runs in a clean state):

- Bay overlap: a second booking that overlaps in the same bay with a different technician fails with `no_bay_overlap`.
- Technician overlap: a second booking for the same technician in a different bay fails with `no_tech_overlap`.
- Touching reserved ranges: a 60-min job at 10:00 (reserved until 11:15, work + buffer) and a booking at 11:15 for the same bay and technician both succeed.
- Buffer blocks: after a 90-min job at 09:00 (reserved until 10:45), a booking for the same technician at 10:30 is refused and one at 10:45 succeeds.
- Cancel frees the slot: book, cancel, then book the same slot again and get 201. Cancelling twice returns 200 with the same record.
- Vehicle overlap: a second booking for the same vehicle that overlaps the first returns 409 `vehicle_busy` with the first appointment's id, even when another bay and technician are free. A direct insert fails on `no_vehicle_overlap`.
- Least-loaded pick: with two qualified technicians and one already booked for 2 hours that day, the booking goes to the other one. With equal load, it goes to the lower id.
- `outside_hours`: a booking that starts before opening or whose work ends after closing returns 422 with `reason: outside_hours`.
- Database guards: inserting a status other than `confirmed` or `cancelled` fails on `status_valid`; a bay from another dealership fails on `bay_in_dealership`, and a technician from another dealership fails on `tech_in_dealership`.
- Vehicle ownership: a vehicle that belongs to another customer returns 422.
- More database guards: a service type with duration 0 or 20 minutes, an opening-hours row with weekday 8 or `opens_at` not before `closes_at`, and an appointment whose `ends_at` lies outside `during` each fail on their `CHECK`.
- Seed then insert: after the seed script runs, a normal insert into dealership, vehicle and appointment succeeds (identity sequences are not behind the seeded ids, so no `23505`).
- Stored work end: after a booking, changing the service type's duration leaves the appointment's `ends_at` and the technician's load count unchanged.

Concurrency tests (real Postgres, real parallel connections):

- One slot, capacity k. Seed k qualified technicians and k compatible bays. Send N > k bookings for the same start at the same time with `asyncio.gather`, each with its own vehicle and its own connection. Expect exactly k responses of 201 and N - k of 409. This holds for any k, because the loop has no fixed attempt cap (see [Data Flow](../design.md#data-flow)). Then query the table and check that no two active rows share a bay or a technician with overlapping `during`.
- Same request twice at once. Seed exactly one qualified technician and one compatible bay, so the losing request has no other candidate. Send the same request twice in parallel. Expect one 201 and one 409 `vehicle_busy` with the first appointment's id, and exactly one row. This checks that the reason does not depend on which index Postgres checks first (see [Data Flow](../design.md#data-flow)).

API tests (httpx `AsyncClient` against the app in process, on the same test database):

- Status codes and `problem+json` bodies for 201, 200, 404, 409 and 422, including `reason` and `request_id`.
- `X-Request-ID` is echoed back when sent and generated when missing.
- A request to a URL with no route is counted in metrics with `route="unmatched"`.
- 503 when the database connection is lost (an engine pointing at a closed port), and 503 when a query hits `statement_timeout`.
- `/health` returns 200, `/ready` returns 200 with the database up, and `/metrics` lists the metric names from [observability.md](observability.md#metrics).
