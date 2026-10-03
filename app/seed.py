import random
from datetime import date, timedelta

from sqlalchemy import func, insert, select

from app.db import Base, engine
from app.models import Sale

REGIONS = ["North", "South", "East", "West"]
PRODUCTS = [f"Product {chr(65 + i)}" for i in range(12)]


def seed(rows: int = 30000, year: int = 2025, seed_value: int = 7) -> int:
    Base.metadata.create_all(engine)
    rnd = random.Random(seed_value)
    start = date(year, 1, 1)
    data = [
        {
            "sold_on": start + timedelta(days=rnd.randint(0, 364)),
            "region": rnd.choice(REGIONS),
            "product": rnd.choice(PRODUCTS),
            "amount_cents": rnd.randint(500, 90000),
        }
        for _ in range(rows)
    ]
    with engine.begin() as conn:
        conn.execute(insert(Sale), data)
    return rows


def ensure_seeded() -> None:
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        empty = not conn.scalar(select(func.count()).select_from(Sale))
    if empty:
        seed()
