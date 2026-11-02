"""Request and response bodies (docs/details/api.md)."""

from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field

from app.repository import AppointmentRecord

# ids are Postgres bigint identities: a value outside 1..2^63-1 can never exist, and
# asyncpg cannot even send one, so it is a 422 here instead of a database error
ID_MAX = 2**63 - 1
Id = Annotated[int, Field(gt=0, le=ID_MAX)]


class BookingIn(BaseModel):
    customer_id: Id
    vehicle_id: Id
    dealership_id: Id
    service_type_id: Id
    start: AwareDatetime  # a start without an offset is a 422, not a guess


class Ref(BaseModel):
    id: int
    name: str


class AppointmentOut(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    dealership_id: int
    service_type_id: int
    bay: Ref
    technician: Ref
    start: datetime
    end: datetime
    reserved_until: datetime
    status: str
    created_at: datetime

    @classmethod
    def from_record(cls, r: AppointmentRecord) -> "AppointmentOut":
        return cls(
            id=r.id,
            customer_id=r.customer_id,
            vehicle_id=r.vehicle_id,
            dealership_id=r.dealership_id,
            service_type_id=r.service_type_id,
            bay=Ref(id=r.bay_id, name=r.bay_name),
            technician=Ref(id=r.technician_id, name=r.technician_name),
            start=r.start,
            end=r.end,
            reserved_until=r.reserved_until,
            status=r.status,
            created_at=r.created_at,
        )
