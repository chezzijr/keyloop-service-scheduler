"""Seed the reference data (Assumption 1) into an empty, migrated database.

Rows are inserted without ids, so on empty tables the ids are 1..n in insert order.
Ids that scripts/demo.sh and the tests rely on:

    dealership     1  Northside Motors, Europe/London
                      open Mon-Fri 08:00-18:00, Sat 08:00-13:00, closed Sunday (local time)
    bay_type       1 lift, 2 alignment, 3 mot_lane
    service_bay    1 Lift 1, 2 Lift 2, 3 Lift 3 (lift); 4 Alignment bay (alignment);
                   5 MOT lane (mot_lane)
    skill          1 general, 2 alignment, 3 mot_tester, 4 ev_hv
    technician     1 Alice (general, alignment)   2 Ben (general, mot_tester)
                   3 Chloe (general, ev_hv)       4 Dan (general, alignment)
    service_type   1 oil_change 45+15     2 annual_service 180+15   3 brake_pads 90+15
                   4 wheel_alignment 60+15 (alignment bay, alignment skill)
                   5 mot_test 60+15 (mot_lane bay, mot_tester skill)
                   6 ev_battery_check 60+15 (lift bay, ev_hv skill)
    customer       1 Emma Clarke, 2 Oliver Bennett, 3 Sophie Turner
    vehicle        1, 2 (customer 1); 3 (customer 2); 4 (customer 3)

Demo cases this allows:
    no_technician  two MOT tests (or two EV checks) at the same time: one qualified technician.
    no_bay         two wheel alignments at the same time: two qualified technicians, one bay.

Run: DATABASE_URL=postgresql+asyncpg://... python -m scripts.seed
"""

import asyncio
import os
from datetime import time

from sqlalchemy import exists, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.models import (
    BayType,
    Customer,
    Dealership,
    OpeningHours,
    ServiceBay,
    ServiceType,
    Skill,
    Technician,
    TechnicianSkill,
    Vehicle,
)

# ISO weekday -> (opens_at, closes_at), local time; Sunday (7) closed
OPENING_HOURS = {d: (time(8), time(18)) for d in range(1, 6)} | {6: (time(8), time(13))}


async def seed(conn: AsyncConnection) -> None:
    """Insert the seed rows on `conn`; the caller owns the transaction."""
    if await conn.scalar(select(exists().select_from(Dealership))):
        raise RuntimeError("database is not empty: dealership already has rows")

    rows = [
        (Dealership, [{"name": "Northside Motors", "timezone": "Europe/London"}]),
        (
            OpeningHours,
            [
                {"dealership_id": 1, "weekday": d, "opens_at": o, "closes_at": c}
                for d, (o, c) in OPENING_HOURS.items()
            ],
        ),
        (BayType, [{"code": c} for c in ("lift", "alignment", "mot_lane")]),
        (
            ServiceBay,
            [
                {"dealership_id": 1, "bay_type_id": t, "name": n}
                for t, n in [
                    (1, "Lift 1"),
                    (1, "Lift 2"),
                    (1, "Lift 3"),
                    (2, "Alignment bay"),
                    (3, "MOT lane"),
                ]
            ],
        ),
        (Skill, [{"code": c} for c in ("general", "alignment", "mot_tester", "ev_hv")]),
        (Technician, [{"dealership_id": 1, "name": n} for n in ("Alice", "Ben", "Chloe", "Dan")]),
        (
            TechnicianSkill,
            [
                {"technician_id": t, "skill_id": s}
                for t, s in [(1, 1), (1, 2), (2, 1), (2, 3), (3, 1), (3, 4), (4, 1), (4, 2)]
            ],
        ),
        (
            ServiceType,
            [
                {
                    "code": code,
                    "name": name,
                    "duration_minutes": minutes,
                    "buffer_minutes": 15,
                    "required_bay_type_id": bay_type,
                    "required_skill_id": skill,
                }
                for code, name, minutes, bay_type, skill in [
                    ("oil_change", "Oil and filter change", 45, 1, 1),
                    ("annual_service", "Full annual service", 180, 1, 1),
                    ("brake_pads", "Brake pads replacement", 90, 1, 1),
                    ("wheel_alignment", "Wheel alignment", 60, 2, 2),
                    ("mot_test", "MOT test", 60, 3, 3),
                    ("ev_battery_check", "EV battery health check", 60, 1, 4),
                ]
            ],
        ),
        (
            Customer,
            [
                {"name": "Emma Clarke", "email": "emma.clarke@example.com"},
                {"name": "Oliver Bennett", "email": "oliver.bennett@example.com"},
                {"name": "Sophie Turner", "email": "sophie.turner@example.com"},
            ],
        ),
        (
            Vehicle,
            [
                {"customer_id": c, "vin": vin, "make": make, "model": model}
                for c, vin, make, model in [
                    (1, "WF0XXXGCDX1234567", "Ford", "Focus"),
                    (1, "KNACC81GFM5012345", "Kia", "Niro EV"),
                    (2, "WVWZZZAUZLW123456", "Volkswagen", "Golf"),
                    (3, "SALGA2AE7KA123456", "Land Rover", "Range Rover"),
                ]
            ],
        ),
    ]
    for model, values in rows:
        await conn.execute(insert(model), values)


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await seed(conn)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
