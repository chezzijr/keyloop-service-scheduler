"""SQLAlchemy mapped classes for the tables in migrations/versions/0001_initial.py.

The migration is the source of truth for the schema and is written by hand; these classes
mirror it for queries and are not used to generate it."""

from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE, ExcludeConstraint, Range
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def id_column() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=False), primary_key=True)


class Dealership(Base):
    __tablename__ = "dealership"

    id: Mapped[int] = id_column()
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class BayType(Base):
    __tablename__ = "bay_type"

    id: Mapped[int] = id_column()
    code: Mapped[str] = mapped_column(Text, unique=True)


class ServiceBay(Base):
    __tablename__ = "service_bay"
    __table_args__ = (UniqueConstraint("id", "dealership_id"),)

    id: Mapped[int] = id_column()
    dealership_id: Mapped[int] = mapped_column(ForeignKey("dealership.id"))
    bay_type_id: Mapped[int] = mapped_column(ForeignKey("bay_type.id"))
    name: Mapped[str] = mapped_column(Text)


class Skill(Base):
    __tablename__ = "skill"

    id: Mapped[int] = id_column()
    code: Mapped[str] = mapped_column(Text, unique=True)


class Technician(Base):
    __tablename__ = "technician"
    __table_args__ = (UniqueConstraint("id", "dealership_id"),)

    id: Mapped[int] = id_column()
    dealership_id: Mapped[int] = mapped_column(ForeignKey("dealership.id"))
    name: Mapped[str] = mapped_column(Text)


class TechnicianSkill(Base):
    __tablename__ = "technician_skill"

    technician_id: Mapped[int] = mapped_column(ForeignKey("technician.id"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"), primary_key=True)


class ServiceType(Base):
    __tablename__ = "service_type"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes > 0 AND duration_minutes % 15 = 0", name="duration_valid"
        ),
        CheckConstraint("buffer_minutes >= 0 AND buffer_minutes % 15 = 0", name="buffer_valid"),
    )

    id: Mapped[int] = id_column()
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    buffer_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    required_skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"))
    required_bay_type_id: Mapped[int] = mapped_column(ForeignKey("bay_type.id"))


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = id_column()
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = id_column()
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    vin: Mapped[str] = mapped_column(Text, unique=True)
    make: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)


class OpeningHours(Base):
    __tablename__ = "opening_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_valid"),
        CheckConstraint("opens_at < closes_at", name="hours_valid"),
    )

    dealership_id: Mapped[int] = mapped_column(ForeignKey("dealership.id"), primary_key=True)
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # ISO, 1 = Monday
    opens_at: Mapped[time] = mapped_column(Time)  # local time of the dealership
    closes_at: Mapped[time] = mapped_column(Time)


ACTIVE = "status <> 'cancelled'"


class Appointment(Base):
    __tablename__ = "appointment"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="status_valid"),
        ForeignKeyConstraint(
            ["service_bay_id", "dealership_id"],
            ["service_bay.id", "service_bay.dealership_id"],
            name="bay_in_dealership",
        ),
        ForeignKeyConstraint(
            ["technician_id", "dealership_id"],
            ["technician.id", "technician.dealership_id"],
            name="tech_in_dealership",
        ),
        CheckConstraint(
            "lower(during) < ends_at AND ends_at <= upper(during)", name="ends_at_in_during"
        ),
        # same order as the migration: the vehicle is checked first
        ExcludeConstraint(
            ("vehicle_id", "="), ("during", "&&"), name="no_vehicle_overlap", where=ACTIVE
        ),
        ExcludeConstraint(
            ("technician_id", "="), ("during", "&&"), name="no_tech_overlap", where=ACTIVE
        ),
        ExcludeConstraint(
            ("service_bay_id", "="), ("during", "&&"), name="no_bay_overlap", where=ACTIVE
        ),
    )

    id: Mapped[int] = id_column()
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"))
    dealership_id: Mapped[int] = mapped_column(ForeignKey("dealership.id"))
    service_type_id: Mapped[int] = mapped_column(ForeignKey("service_type.id"))
    service_bay_id: Mapped[int] = mapped_column(BigInteger)
    technician_id: Mapped[int] = mapped_column(BigInteger)
    # half-open [start, end of work + buffer)
    during: Mapped[Range[datetime]] = mapped_column(TSTZRANGE)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # planned end of work
    status: Mapped[str] = mapped_column(Text, server_default=text("'confirmed'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
