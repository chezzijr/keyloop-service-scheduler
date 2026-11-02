# Unified Service Scheduler (Keyloop take-home, Scenario A)

A backend API that books service appointments and checks that a service bay and a qualified technician are both free for the whole duration of the service.

Design: [docs/design.md](docs/design.md)

Reviewer walkthrough and video script: [docs/demo.md](docs/demo.md)

## Prerequisites

- Docker with the compose plugin, to run the API and Postgres.
- [uv](https://docs.astral.sh/uv/), to run the tests. uv installs Python 3.14 if needed.
- curl, jq and GNU date, for the demo script. On macOS, install coreutils and put its `gnubin` directory first on `PATH`.

## Run

```sh
docker compose up -d --build     # Postgres 18 + the API on http://localhost:8000
docker compose run --rm seed     # reference data: dealership, bays, technicians, service types
```

Open http://localhost:8000/docs. This Swagger UI is the mocked client: it lists every endpoint and can send requests.

Run the demo story with cURL:

```sh
./scripts/demo.sh
```

It books an appointment, reads it, shows a `vehicle_busy` and a `no_technician` conflict, cancels the first booking and books its slot again. Each step prints the HTTP status, and the script stops with exit code 1 if a status is not the expected one. The slot is the next weekday at least 2 days ahead, 10:00 London time. Set `START` (UTC, on the 15-minute grid, inside opening hours) to use another slot, for example `START=2026-11-10T10:00:00Z ./scripts/demo.sh`. The demo needs fresh seed data: a second run on the same slot stops at step 1 and prints how to reset.

Show that parallel requests never double-book:

```
./scripts/race.sh
```

It sends 10 oil-change bookings at the same moment for 3 free lifts, expects exactly 3 `201` and 7 `409`, then asks Postgres whether any bay or technician is double-booked (expected `0`). Its slot is 14:00 London time, so it does not clash with `demo.sh`.

Stop and delete the data:

```sh
docker compose down -v
```

## Test

```sh
uv sync
uv run pytest
```

Docker must be running: testcontainers starts a `postgres:18` container for the test session.

- `tests/unit`: booking rules (times, grid, opening hours, DST). No database.
- `tests/integration`: schema, constraints and the booking service on a real Postgres.
- `tests/concurrency`: parallel bookings never double-book a bay, a technician or a vehicle.
- `tests/api`: HTTP status codes, problem+json bodies, request ids, metrics and readiness.
- `tests/e2e`: the demo story, run in process.

CI runs the same tests on every push and pull request (`.github/workflows/test.yml`).

## Repository layout

- `app/`: the FastAPI app (routers, booking service, repository, models).
- `migrations/`: Alembic migrations. `scripts/`: seed data and the demo.
- `docs/design.md`: the system design. `docs/details/`: assumptions, data model, API, observability and test details.
- `docs/ai-log.md`: log of the work with AI.

## AI Collaboration Narrative

To be written.
