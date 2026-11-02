"""Request and response bodies (docs/details/api.md)."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel

from app.repository import AppointmentRecord


class BookingIn(BaseModel):
    customer_id: int
    vehicle_id: int
    dealership_id: int
    service_type_id: int
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
