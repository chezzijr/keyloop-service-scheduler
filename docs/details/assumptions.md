# Assumptions

Part of the [system design](../design.md).

Each line is a decision taken where the brief is silent.

1. Reference data (dealerships, bays, technicians, skills, service types, customers, vehicles) lives in other Keyloop systems. Here it is seeded by a script; there are no CRUD endpoints for it.
2. The client does not choose the bay or technician. The system assigns both.
3. Technician choice: among qualified technicians on shift, pick the least-loaded one. Ties go to the lowest technician id.
4. Load = sum of booked work minutes (`duration_minutes`, not the buffer) of active appointments for that technician on that local day. It is read without a lock, so it affects fairness only, never correctness.
5. Bay choice: the compatible bay with the lowest id that is free.
6. If the insert fails because a bay or technician was taken in the meantime (SQLSTATE `23P01`), the service tries the next candidate pair.
7. All timestamps are stored as `timestamptz` in UTC. Each dealership has an IANA time zone (for example `Europe/London`) used for opening hours, shifts and "local day".
8. Start times sit on a 15-minute grid (minute 0, 15, 30 or 45, zero seconds) in UTC.
9. Duration comes from the service type. The client does not send an end time. End = start + duration. This is a planned end, not a measured one (Assumption 24).
10. Each service type requires one bay type (for example `lift`, `alignment`) and one technician skill (for example `ev_hv`). A bay is compatible if its type matches. A technician is qualified if they have the skill.
11. One appointment covers exactly one service type.
12. Dealership opening hours and technician shifts are a weekly pattern: weekday (ISO, 1 = Monday) with a local start and end time, one window per weekday (no split shifts). So lunch breaks are not modelled: a technician counts as available for the whole shift. The 15-minute buffer (Assumption 24) is slack for overruns, not a break, because an overrun uses it up. There are no holidays, closures or one-off exceptions.
13. The work part `[start, end)` must fall inside the dealership's opening hours and inside the assigned technician's shift for that weekday. The buffer (Assumption 24) may run past closing time. An appointment does not cross midnight local time.
14. Booking window: the start must not be in the past and must be at most 90 days ahead.
15. Time ranges are half-open. The overlap rules use the reserved range `[start, reserved_until)` (work + buffer, Assumption 24). A reserved range ending at 10:00 does not conflict with one starting at 10:00.
16. The vehicle must belong to the customer in the request.
17. A booking that conflicts with other bookings returns 409 with a `reason`: `no_bay`, `no_technician` or `idempotency_key_reused`. A start outside opening hours returns 422 with `reason: outside_hours`, because it depends on configuration, not on other bookings, so retrying can never succeed.
18. Idempotency: `POST /appointments` requires an `Idempotency-Key` header. The key is stored in a UNIQUE `idempotency_key` column with a `request_hash` (SHA-256 of the canonical JSON body).
19. Same key and same body returns 200 with the original appointment. Same key and a different body returns 409 `idempotency_key_reused`.
20. Cancel sets `status = cancelled` and `cancelled_at`; the row is kept. Cancelled rows no longer block the bay or technician. Cancelling an already cancelled appointment returns 200 with the same record.
21. Errors use RFC 9457 `application/problem+json`.
22. Request validation errors return 422. This covers an off-grid start, a start in the past, a start more than 90 days ahead, work outside opening hours (`reason: outside_hours`), a vehicle not owned by the customer, unknown reference ids and a missing `Idempotency-Key`. A request whose key was already used gets its replay before these checks run (see [Data Flow](../design.md#data-flow)).
23. There is no authentication or authorisation in this service. An API gateway in front of it is assumed to handle that.
24. Nobody knows in advance how long a real repair takes. Workshops book against a planned duration: a standard labour time per job. So `duration_minutes` is planned time, and each service type also has `buffer_minutes` (default 15, a multiple of 15) as slack for small overruns. The bay and technician are reserved for `[start, start + duration + buffer)`. This range is what `during` stores and what the overlap rules check. The API returns both `end` (planned end of work) and `reserved_until` (end of the buffer).
25. The system stores the plan only. If a job overruns past its buffer, nothing moves automatically. The service advisor (the person who manages bookings at the front desk) tells the next customer, or cancels and rebooks. See "Operational delays" in [Data Flow](../design.md#data-flow).
26. Jobs that can last several days are out of scope. That covers repairs that start with fault diagnosis, jobs waiting for parts, and body repair. Every appointment here is one known service type with a planned duration inside one working day.
