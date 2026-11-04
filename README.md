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

See every booking in the database, with names and local times:

```
./scripts/board.sh
```

It reads the `appointment` table directly with `psql`. `work_ends` is the planned end of the work and `bay_free` the end of the cleanup buffer. Cancelled bookings stay listed with their status.

The three scripts share their formatting in `scripts/lib/pretty.sh`. They print colours only to a terminal; set `NO_COLOR=1` to turn them off.

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

I used Claude Code (Opus 5.5) as the main collaborator, from the first design question to the last demo script. The AI wrote most of the text and code. My job was to set the direction, make the decisions, and not accept any claim that had not been checked. The full record, with dates, is in [docs/ai-log.md](docs/ai-log.md).

### Strategy: how I guided the AI

- **A fixed workflow, with state kept in files.** Every step followed the same order: questions until the problem was clear, then a written design and task plan, my approval, a build task by task, a review, and a walkthrough as a user. The plan, the design doc and the log lived in files, so a new session could continue from them instead of from chat memory.
- **The AI proposes, I decide.** The AI gave options with trade-offs, and I chose. Examples: FastAPI and PostgreSQL, with an exclusion constraint against overlaps; automatic choice of bay and technician; least-loaded technician over a "pluggable strategy" the AI itself called YAGNI; and the dealership lock out of three fixes for the deadlock.
- **I questioned the business logic.** The first design assumed every job has a known end time. I asked how a repair can have an end time, what happens with multi-day jobs, and what happens to the next booking when a job overruns. That added planned durations, a per-service cleanup buffer, and explicit assumptions. In the same way I found that one opening window per day silently ignores lunch breaks, and made that a documented limitation.
- **Small scope on purpose.** When I asked for all four optional extras, the AI showed they were about half the build effort. Later I asked where the design could be simpler to explain, and cut idempotency keys, the availability query, technician shifts and OpenTelemetry tracing. Each one keeps a "how to build it" paragraph in Future Work. Cutting idempotency opened a real gap: a resent request could book the same car twice. A second review session caught it, and I added the third exclusion constraint, on the vehicle.

### Verification: how I checked and refined the output

- **Run it, don't trust it.** Every claim about Postgres was run on a real `postgres:18`. Two design claims were wrong until they were run: that a same-key race always ends in `23505` (it depends on index creation order), and that the savepoint loop handles every race through `23P01`.
- **Tests first, and proof that they can fail.** Each task started with a failing test. Where a test passed at once, the AI broke the code on purpose to show the test turns red. The concurrency test found the deadlock (`40P01`) that no reading of the design had found. With the lock removed, the deadlock came back. With the lock and the technician constraint both removed, the SQL check found 3 double-booked pairs.
- **Independent reviewers.** I ran a second AI session to review the design cold, twice, and had the first session check each finding before fixing it. For the code, `/kit:review` runs prosecutor agents that see only the diff, a defender agent that tries to refute each charge, and a judge. It found the same vehicle booked at two dealerships at once could still deadlock, and inputs that gave a 500 instead of a 422.
- **Use it as a user.** I asked whether the AI had run the system the way a reviewer would. It said honestly what it had not run. The walkthrough then found three problems no test had caught: the first Swagger click would have returned a 422, the OpenAPI spec did not document the error responses, and every request was logged twice.

Where the AI was wrong, and what caught it:

| What was wrong | Caught by | Fix |
|---|---|---|
| Design assumed known end times and no overruns | my questions on the draft | planned durations, buffer, documented assumptions |
| Parallel inserts could deadlock (`40P01` → 500) | concurrency test on Postgres | advisory lock per dealership |
| The lock did not cover one vehicle at two dealerships | adversarial code review | second advisory lock per vehicle |
| 503 handled only a closed port, not other lost connections | tests written against the promise in the API doc | broader connection-error handling, 3 tests |
| Swagger example missing, error responses undocumented | walkthrough as a user | tested example and problem+json responses |

### How I ensured the final quality

- 123 tests on a real PostgreSQL 18: unit, integration, concurrency (parallel bookings, 20 rounds each), API and end-to-end. CI runs them on every push.
- After each task the AI showed me the diff and the test output. Nothing was committed without my yes, one logical change per commit.
- The docs say plainly what is not built ("designed, not built") and which low-severity items are known and left open.

What I learned: the AI is fast and often right, but it states guesses with the same confidence as facts. Trust came from running things (a real database, parallel requests, a real browser), from reviewers who had no stake in the code, and from asking the AI what it had *not* checked.
