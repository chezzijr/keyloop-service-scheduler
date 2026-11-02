# API

Part of the [system design](../design.md).

## Booking examples

Booking request:

```http
POST /appointments
Content-Type: application/json

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

Sending the same request again returns `409 Conflict` with `reason` `vehicle_busy` and `appointment_id: 1001`, the booking this vehicle already has. No second appointment is created.

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
- `200 OK`: a read or a cancel.
- `404 Not Found`: appointment id does not exist.
- `409 Conflict`: the request is valid but conflicts with other bookings. `reason` is one of `no_bay`, `no_technician`, `vehicle_busy`.
- `422 Unprocessable Content`: validation failed. Examples: start not on the 15-minute grid, start in the past, start more than 90 days ahead, work outside opening hours (`reason: outside_hours`), vehicle not owned by the customer, unknown reference id. The problem body lists the failing fields in `errors`.
- `503 Service Unavailable`: `/ready` when the database cannot be reached, or any request whose query hits the database `statement_timeout` (see [Scalability, Performance and Reliability](../design.md#scalability-performance-and-reliability)).

All error bodies use RFC 9457 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, plus `reason` for 409 and for 422 `outside_hours`, `appointment_id` for `vehicle_busy`, and `request_id` for tracing.
