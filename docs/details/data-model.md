# Data Model

Part of the [system design](../design.md).

## Entities and columns

```mermaid
erDiagram
    DEALERSHIP ||--o{ SERVICE_BAY : has
    DEALERSHIP ||--o{ TECHNICIAN : employs
    DEALERSHIP ||--o{ OPENING_HOURS : "open during"
    DEALERSHIP ||--o{ APPOINTMENT : hosts
    BAY_TYPE ||--o{ SERVICE_BAY : "typed as"
    BAY_TYPE ||--o{ SERVICE_TYPE : "required by"
    SKILL ||--o{ TECHNICIAN_SKILL : "held via"
    SKILL ||--o{ SERVICE_TYPE : "required by"
    TECHNICIAN ||--o{ TECHNICIAN_SKILL : has
    TECHNICIAN ||--o{ TECHNICIAN_SHIFT : works
    TECHNICIAN ||--o{ APPOINTMENT : "assigned to"
    SERVICE_BAY ||--o{ APPOINTMENT : "assigned to"
    SERVICE_TYPE ||--o{ APPOINTMENT : "booked as"
    CUSTOMER ||--o{ VEHICLE : owns
    CUSTOMER ||--o{ APPOINTMENT : books
    VEHICLE ||--o{ APPOINTMENT : "serviced in"

    DEALERSHIP {
        bigint id PK
        text name
        text timezone "IANA, e.g. Europe/London"
    }
    BAY_TYPE {
        bigint id PK
        text code UK "e.g. lift, alignment"
    }
    SERVICE_BAY {
        bigint id PK
        bigint dealership_id FK
        bigint bay_type_id FK
        text name
    }
    SKILL {
        bigint id PK
        text code UK "e.g. ev_hv"
    }
    TECHNICIAN {
        bigint id PK
        bigint dealership_id FK
        text name
    }
    TECHNICIAN_SKILL {
        bigint technician_id PK, FK
        bigint skill_id PK, FK
    }
    SERVICE_TYPE {
        bigint id PK
        text code UK
        text name
        int duration_minutes "planned, multiple of 15"
        int buffer_minutes "default 15, multiple of 15"
        bigint required_skill_id FK
        bigint required_bay_type_id FK
    }
    CUSTOMER {
        bigint id PK
        text name
        text email
    }
    VEHICLE {
        bigint id PK
        bigint customer_id FK
        text vin UK
        text make
        text model
    }
    OPENING_HOURS {
        bigint dealership_id PK, FK
        smallint weekday PK "ISO 1-7"
        time opens_at "local"
        time closes_at "local"
    }
    TECHNICIAN_SHIFT {
        bigint technician_id PK, FK
        smallint weekday PK "ISO 1-7"
        time starts_at "local"
        time ends_at "local"
    }
    APPOINTMENT {
        bigint id PK
        bigint customer_id FK
        bigint vehicle_id FK
        bigint dealership_id FK
        bigint service_type_id FK
        bigint service_bay_id FK
        bigint technician_id FK
        tstzrange during "half-open, start to end + buffer"
        text status "confirmed or cancelled"
        text idempotency_key UK
        text request_hash "SHA-256 of body"
        timestamptz created_at
        timestamptz cancelled_at "null unless cancelled"
    }
```

## Seed service types

Seed service types. Durations are illustrative, in the style of standard labour times, not real figures from a manufacturer. The set covers one shared bay type (`lift`), two special bays (`alignment`, `mot_lane`) for the `no_bay` case, and rare skills for the `no_technician` case.

| Service type | Duration | Buffer | Bay type | Skill |
|---|---|---|---|---|
| Oil and filter change | 45 min | 15 min | `lift` | `general` |
| Full annual service | 180 min | 15 min | `lift` | `general` |
| Brake pads replacement | 90 min | 15 min | `lift` | `general` |
| Wheel alignment | 60 min | 15 min | `alignment` | `alignment` |
| MOT test (UK yearly road-safety test) | 60 min | 15 min | `mot_lane` | `mot_tester` |
| EV battery health check | 60 min | 15 min | `lift` | `ev_hv` |

## Other database guards

```sql
-- the overlap rules depend on status, so a typo like 'canceled' must be impossible
ALTER TABLE appointment
  ADD CONSTRAINT status_valid CHECK (status IN ('confirmed', 'cancelled'));

-- the bay and technician must belong to the appointment's dealership
-- (service_bay and technician each get UNIQUE (id, dealership_id) for this)
ALTER TABLE appointment
  ADD CONSTRAINT bay_in_dealership
  FOREIGN KEY (service_bay_id, dealership_id) REFERENCES service_bay (id, dealership_id);

ALTER TABLE appointment
  ADD CONSTRAINT tech_in_dealership
  FOREIGN KEY (technician_id, dealership_id) REFERENCES technician (id, dealership_id);
```

An insert that overlaps an active appointment on the same bay or technician fails with SQLSTATE `23P01`, and the error names the constraint, so the service knows which resource clashed. The booking service checks that the vehicle belongs to the customer before the insert. A composite foreign key `(vehicle_id, customer_id)` could enforce this in the database. It is not used because a vehicle can change owner later, and a past appointment must keep the customer who booked it.
