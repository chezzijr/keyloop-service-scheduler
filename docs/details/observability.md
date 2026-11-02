# Observability Detail

Part of the [system design](../design.md).

## Log fields

| Field | Meaning |
|---|---|
| `ts` | UTC timestamp, ISO 8601 |
| `level` | `info`, `warning` or `error` |
| `request_id` | from `X-Request-ID` or generated |
| `method` | HTTP method |
| `route` | route template, for example `/appointments/{id}`, not the raw path; `unmatched` for a URL with no route |
| `status` | HTTP status code |
| `latency_ms` | time spent in the app for this request |
| `appointment_id` | set when a booking or cancel touches an appointment |
| `reason` | set on a 409 (`no_bay`, `no_technician`, `vehicle_busy`) or a 422 `outside_hours` |
| `attempts` | number of insert attempts in the booking loop (1 to \|T\| + \|B\|) |

## Label cardinality

Label cardinality. Labels only take values from a small, fixed set: route templates, methods, status codes, reasons and dealership ids. Customer ids, vehicle ids, appointment ids and request ids are never labels. Each new label value creates a new time series in Prometheus, so an unbounded id would grow memory without limit. Those ids belong in logs.

## Traces (designed, not built)

Traces (designed, not built). OpenTelemetry would instrument FastAPI (one span per request) and SQLAlchemy (one child span per query), so a slow booking would show whether the time went to the candidate queries, the insert loop or a wait on another transaction. Spans would go over OTLP to a viewer such as Jaeger, switched on by `OTEL_EXPORTER_OTLP_ENDPOINT`. It is not built because this system has one service and one database: the `request_id` in logs, `latency_ms` and the latency histogram already find a slow request. Tracing pays off once a request crosses several services.

## Metrics

Metrics:

| Metric | Type | Labels | What it shows |
|---|---|---|---|
| `http_request_duration_seconds` | histogram | `route`, `method`, `status` | request rate, error rate and latency per endpoint |
| `appointments_booked_total` | counter | `dealership_id` | successful bookings (201 only) |
| `booking_rejected_total` | counter | `reason` | 409 responses by reason |
| `booking_insert_attempts` | histogram | none | insert attempts per booking; a value above 1 means a race was lost |
| `appointments_cancelled_total` | counter | none | cancels that changed a row |

## Example alerts

Example alerts. These are written as rules a Prometheus setup would run. No alerting stack is part of this build.

| Alert | Condition (example) | Why |
|---|---|---|
| Rejection rate by reason | `booking_rejected_total{reason="no_technician"}` above 30% of booking requests over 30 minutes | Dealership is short of qualified staff, or seed data is wrong. Split by reason tells a staff shortage apart from a bay shortage. |
| Slow bookings | p95 of `http_request_duration_seconds{route="/appointments",method="POST"}` above 500 ms for 10 minutes | Database slow, pool exhausted, or long lock waits. |
| Contention | share of bookings with `booking_insert_attempts` above 1 grows above 5% | Many requests race for the same slots. A sign to look at hot dealerships before users see more 409s. |
| Not ready | `/ready` failing on any instance for 2 minutes | Database unreachable from that instance. |

The thresholds are starting points, not tuned values. They would be set from real traffic.
