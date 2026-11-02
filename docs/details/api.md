# API

Part of the [system design](../design.md).

## Availability query

`GET /availability` query parameters:

- `dealership_id`: the dealership.
- `service_type_id`: decides duration, bay type and skill.
- `date`: a local date in the dealership's time zone, for example `2026-11-10`.

The response lists the 15-minute start times on that date where at least one qualified technician and one compatible bay are free for the whole duration. It is advisory. The booking insert is the final check.

## Booking examples

Booking request:

```http
POST /appointments
Content-Type: application/json
Idempotency-Key: 6f1c2a0e-8b7d-4c1e-9a55-2d3f4e5a6b7c

{
  "customer_id": 42,
  "vehicle_id": 314,
  "dealership_id": 1,
  "service_type_id": 3,
  "start": "2026-11-10T09:00:00Z"
}
```

Response `201 Created`:

```json
{
  "id": 1001,
  "customer_id": 42,
  "vehicle_id": 314,
  "dealership_id": 1,
  "service_type_id": 3,
  "bay": { "id": 7, "name": "Bay 2" },
  "technician": { "id": 12, "name": "Minh Tran" },
  "start": "2026-11-10T09:00:00Z",
  "end": "2026-11-10T10:30:00Z",
  "reserved_until": "2026-11-10T10:45:00Z",
  "status": "confirmed",
  "created_at": "2026-11-01T08:15:02Z"
}
```

Sending the same request again with the same `Idempotency-Key` returns `200 OK` with this same body. No second appointment is created.

Response `409 Conflict` when no qualified technician is free:

```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json

{
  "type": "https://example.com/problems/booking-conflict",
  "title": "Appointment cannot be booked",
  "status": 409,
  "detail": "No technician with skill ev_hv is free from 09:00 to 10:15 UTC on 2026-11-10 (EV battery health check: 60 min work + 15 min buffer).",
  "instance": "/appointments",
  "reason": "no_technician",
  "request_id": "b7e4c1d2a9f0"
}
```

## Status codes

Status codes:

- `201 Created`: appointment booked.
- `200 OK`: idempotent replay of a booking, a read, a cancel, or an availability result.
- `404 Not Found`: appointment id does not exist.
- `409 Conflict`: the request is valid but conflicts with other bookings. `reason` is one of `no_bay`, `no_technician`, `idempotency_key_reused`.
- `422 Unprocessable Content`: validation failed. Examples: start not on the 15-minute grid, start in the past, start more than 90 days ahead, work outside opening hours (`reason: outside_hours`), vehicle not owned by the customer, unknown reference id, missing `Idempotency-Key`. The problem body lists the failing fields in `errors`.
- `503 Service Unavailable`: `/ready` when the database cannot be reached, or any request whose query hits the database `statement_timeout` (see [Scalability, Performance and Reliability](../design.md#scalability-performance-and-reliability)).

All error bodies use RFC 9457 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, plus `reason` for 409 and for 422 `outside_hours`, and `request_id` for tracing.
