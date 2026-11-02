# Demo walkthrough

A reviewer can follow this page in about 10 minutes. It is also the script for the video. Every step names the requirement it shows. The client is mocked, as the brief allows for a backend submission: the OpenAPI page, cURL scripts and the test suite stand in for a front end.

Prerequisites are in the [README](../README.md#prerequisites). All commands run from the repository root.

## 1. Start the system

```
docker compose up -d --build
docker compose run --rm seed
```

Postgres 18 and the API start; the API runs the migration first. The seed adds one dealership (Europe/London, open Mon-Fri 08:00-18:00), 5 bays, 4 technicians with skills, 6 service types, customers and vehicles. The ids are listed at the top of [scripts/seed.py](../scripts/seed.py).

## 2. Book through the mocked client (requirement 1 and 3)

Open http://localhost:8000/docs. This is the OpenAPI page generated from the code.

1. Open `POST /appointments`, click "Try it out". The example body is a brake pads booking for vehicle 1 on a weekday 7 days ahead. Click "Execute".
2. The response is `201` with the assigned bay and technician, `end` (planned end of work) and `reserved_until` (end of the 15-minute buffer).
3. Click "Execute" again with the same body. The response is `409` with `reason: vehicle_busy` and the `appointment_id` of the first booking: a resent request never creates a second booking.
4. Change `start` to a time with seconds, or to a Sunday, and execute. The response is `422` with the failing field in `errors` (or `reason: outside_hours`).

Every error is `application/problem+json` and carries a `request_id`.

## 3. The booking story in a terminal (requirement 2)

```
./scripts/demo.sh
```

Six steps, each with its HTTP status: book, read, the same vehicle again (`vehicle_busy`), two EV checks at once when only one technician has the `ev_hv` skill (`no_technician`), cancel, and book the freed slot again (same bay and technician). It ends with `/ready` and the booking counters from `/metrics`.

## 4. No double-booking under parallel requests (requirement 2)

```
./scripts/race.sh
```

Ten oil-change bookings are sent at the same moment, one per fleet vehicle. Only 3 lifts exist, so exactly 3 get `201` and 7 get `409 no_bay`. The script then asks Postgres directly for any two active appointments that share a bay or technician at overlapping times: the answer is `0`. The guarantee comes from three exclusion constraints in the database (see [Concurrency and Correctness](design.md#concurrency-and-correctness)), so it holds with any number of API instances.

Both scripts expect fresh seed data. A second run on the same data stops and prints the reset command: `docker compose down -v && docker compose up -d --build && docker compose run --rm seed`.

## 5. Observability

Follow one request by its id:

```
curl -si localhost:8000/appointments/999 -H 'X-Request-ID: my-test-1'
docker compose logs --no-log-prefix api | grep my-test-1
```

The `404` response has the header `x-request-id: my-test-1`, the problem body has `"request_id": "my-test-1"`, and the JSON access log line has the same id, the route template, the status and `latency_ms`.

All log lines are JSON (the first lines are the migration):

```
docker compose logs --no-log-prefix api | jq -R -c 'fromjson? // empty'
```

Metrics in Prometheus format:

```
curl -s localhost:8000/metrics | grep -E '^(appointments_|booking_rejected|http_request_duration_seconds_count)'
```

After the two scripts: `appointments_booked_total`, `booking_rejected_total` by reason, `appointments_cancelled_total`, and request counts per route template.

Readiness versus liveness when the database goes away:

```
docker compose stop db
curl -s localhost:8000/ready     # 503 problem+json: "The database cannot be reached"
curl -s localhost:8000/health    # 200: the process is fine, so it is not restarted
docker compose start db
curl -s localhost:8000/ready     # 200 again (retry for a few seconds while Postgres starts)
```

## 6. Tests

```
uv sync
uv run pytest -q
```

About 120 tests in a few seconds: unit (rules, DST), integration on a real `postgres:18` (constraints, booking service), concurrency (parallel bookings, 20 rounds each), API (status codes, problem bodies, request ids, metrics), and an end-to-end run of the demo story. The full list is in [details/testing.md](details/testing.md).

## Stop

```
docker compose down -v
```

## Video outline (5-10 minutes)

| Time | Part | Show |
|---|---|---|
| 0:00 | Intro | who I am, Scenario A, backend implemented and client mocked |
| 0:45 | Design | [design.md](design.md): architecture diagram, Data Flow sequence, the three exclusion constraints |
| 2:15 | Implementation | layers (router, service, repository), the savepoint insert loop, the dealership and vehicle locks |
| 3:30 | Demo | steps 2 to 5 above: Swagger booking and 409, `demo.sh`, `race.sh`, request id in the logs, `/ready` with the database stopped |
| 6:00 | AI collaboration | from [ai-log.md](ai-log.md): how I planned and verified with the AI; the deadlock the concurrency test found; the gaps the review found |
| 8:00 | Tests | `uv run pytest -q` |
| 8:30 | Lessons | what was hard, what I would build next ([Future Work](design.md#out-of-scope-and-future-work)) |
