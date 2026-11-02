# Testing Strategy: test cases

Part of the [system design](../design.md).

Unit tests (no database, fast):

- End time: `end` = start + `duration_minutes`, `reserved_until` = `end` + `buffer_minutes`, and the half-open range `[start, reserved_until)`.
- Buffer near closing: a job whose work ends exactly at closing time is accepted even though its buffer runs past it.
- Request validation: a start off the 15-minute grid, with seconds, in the past, or more than 90 days ahead is rejected with 422. The clock is injected, so tests do not depend on today's date.
- Slot generation for one local date: the first and last start inside opening hours, a service longer than the window gives no slots, and a closed weekday gives an empty list.
- DST: slot generation in `Europe/London` on 2026-10-25 (a Sunday), the day clocks go back. The test passes its own Sunday opening window to the slot function, so it does not depend on seed data. Opening at 08:00 local is 07:00 UTC on 2026-10-24 and 08:00 UTC on 2026-10-25. The test checks the UTC starts of both days. The clock is fixed to a date before them, so the past-start rule does not drop the slots.
- Reason precedence: empty tech list gives `no_technician` even when bays are also empty. Empty bay list only gives `no_bay`. 

Integration tests on real Postgres (testcontainers starts `postgres:18`; Alembic builds the schema; each test runs in a clean state):

- Bay overlap: a second booking that overlaps in the same bay with a different technician fails with `no_bay_overlap`.
- Technician overlap: a second booking for the same technician in a different bay fails with `no_tech_overlap`.
- Touching reserved ranges: a 60-min job at 10:00 (reserved until 11:15, work + buffer) and a booking at 11:15 for the same bay and technician both succeed.
- Buffer blocks: after a 90-min job at 09:00 (reserved until 10:45), a booking for the same technician at 10:30 is refused and one at 10:45 succeeds.
- Cancel frees the slot: book, cancel, then book the same slot again and get 201. Cancelling twice returns 200 with the same record.
- Idempotent replay: the same key and body returns 200 with the same appointment id, and the table still has one row. The same key with a different body returns 409 `idempotency_key_reused`.
- Least-loaded pick: with two qualified technicians and one already booked for 2 hours that day, the booking goes to the other one. With equal load, it goes to the lower id.
- `outside_hours`: a booking that starts before opening or whose work ends after closing returns 422 with `reason: outside_hours`.
- Late retry: a booking replayed with the same key after its start time has passed returns 200 with the original, not 422.
- Database guards: inserting a status other than `confirmed` or `cancelled` fails on `status_valid`; a bay from another dealership fails on `bay_in_dealership`, and a technician from another dealership fails on `tech_in_dealership`.
- Shifts: a booking inside opening hours but outside every qualified technician's shift returns 409 `no_technician`.
- Vehicle ownership: a vehicle that belongs to another customer returns 422.

Concurrency tests (real Postgres, real parallel connections):

- One slot, capacity k. Seed k qualified technicians and k compatible bays. Send N > k bookings for the same start at the same time with `asyncio.gather`, each with its own key and its own connection. Expect exactly k responses of 201 and N - k of 409. This holds for any k, because the loop has no fixed attempt cap (see [Data Flow](../design.md#data-flow)). Then query the table and check that no two active rows share a bay or a technician with overlapping `during`.
- Same key twice at once. Seed exactly one qualified technician and one compatible bay, so a lost race has no other candidate to fall back to. Send two identical requests with the same `Idempotency-Key` in parallel. Expect one 201 and one 200 with the same appointment id, and exactly one row. This is the case that returned 409 before the key lock was added (see [Concurrency and Correctness](../design.md#concurrency-and-correctness)).

API tests (httpx `AsyncClient` against the app in process, on the same test database):

- Status codes and `problem+json` bodies for 201, 200, 404, 409 and 422, including `reason` and `request_id`.
- `X-Request-ID` is echoed back when sent and generated when missing.
- `/health` returns 200, `/ready` returns 200 with the database up, and `/metrics` lists the metric names from [observability.md](observability.md#metrics).
