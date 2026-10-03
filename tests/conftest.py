import os
import tempfile

TMP = tempfile.mkdtemp(prefix="reports_test_")
os.environ.update({"DATABASE_URL": "sqlite://", "CELERY_EAGER": "1", "STEP_DELAY": "0", "REPORTS_DIR": TMP, "BROKER_DIR": TMP + "/broker"})

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Report, Sale
from app.seed import seed

ROWS = 600


@pytest.fixture(scope="module", autouse=True)
def database():
    Base.metadata.drop_all(engine)
    seed(rows=ROWS)
    yield


@pytest.fixture(autouse=True)
def clean_reports():
    with SessionLocal() as db:
        db.query(Report).delete()
        db.commit()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def total_cents() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.sum(Sale.amount_cents)))


def make_report(**kw) -> int:
    with SessionLocal() as db:
        r = Report(kind=kw.pop("kind", "top_products"), year=kw.pop("year", 2025), status=kw.pop("status", "queued"), progress=0, attempts=0, **kw)
        db.add(r)
        db.commit()
        return r.id


def get_report(rid: int) -> Report:
    with SessionLocal() as db:
        return db.get(Report, rid)
