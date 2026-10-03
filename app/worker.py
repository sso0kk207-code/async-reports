"""Celery. Воркер: celery -A app.worker worker --pool=solo   (по умолчанию брокер - файловая система, Redis не нужен)."""
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from celery import Celery
from sqlalchemy import update

from app.builders import BUILDERS
from app.db import SessionLocal
from app.models import Report

OUT_DIR = Path(os.getenv("REPORTS_DIR", "reports_out"))
STEP_DELAY = float(os.getenv("STEP_DELAY", "0.6"))  # имитация тяжёлых этапов, чтобы прогресс был виден
MAX_RETRIES = 3
BROKER_DIR = Path(os.getenv("BROKER_DIR", "broker"))

celery_app = Celery("reports", broker=os.getenv("CELERY_BROKER_URL", "filesystem://"))
celery_app.conf.update(
    task_always_eager=os.getenv("CELERY_EAGER", "0") == "1",
    task_acks_late=True,              # подтверждение после выполнения: при падении воркера задача не потеряется
    worker_prefetch_multiplier=1,     # долгие задачи: не забираем лишнего про запас
    task_serializer="json",
    broker_connection_retry_on_startup=True,
    broker_transport_options={"data_folder_in": str(BROKER_DIR / "data"), "data_folder_out": str(BROKER_DIR / "data"), "control_folder": str(BROKER_DIR / "control")},
)
if celery_app.conf.broker_url == "filesystem://":
    (BROKER_DIR / "data").mkdir(parents=True, exist_ok=True)
    (BROKER_DIR / "control").mkdir(parents=True, exist_ok=True)


class Cancelled(Exception):
    pass


def _set(db, report_id: int, **values) -> None:
    db.execute(update(Report).where(Report.id == report_id).values(**values))
    db.commit()


def _checkpoint(db, report_id: int, progress: int) -> None:
    """Между этапами: отмена пользователем останавливает работу, прогресс обновляется."""
    status = db.query(Report.status).filter(Report.id == report_id).scalar()
    if status == "cancelled":
        raise Cancelled()
    _set(db, report_id, progress=progress)
    time.sleep(STEP_DELAY)


@celery_app.task(bind=True, name="reports.generate", max_retries=MAX_RETRIES)
def generate_report(self, report_id: int) -> str:
    with SessionLocal() as db:
        # Идемпотентный захват: дубль сообщения из брокера или отменённая задача не будут выполнены второй раз
        claim = db.execute(update(Report).where(Report.id == report_id, Report.status == "queued").values(status="running", attempts=Report.attempts + 1))
        db.commit()
        if claim.rowcount != 1:
            return "skipped"
        report = db.get(Report, report_id)
        started = time.perf_counter()
        try:
            _checkpoint(db, report_id, 10)  # этап 1: подготовка
            path = OUT_DIR / f"report-{report_id}-{report.kind}-{report.year}.xlsx"
            _checkpoint(db, report_id, 35)  # этап 2: запрос агрегатов
            processed = BUILDERS[report.kind](db, report.year, path, attempt=report.attempts)
            _checkpoint(db, report_id, 85)  # этап 3: оформление файла
            _set(db, report_id, status="done", progress=100, rows_processed=processed, file_path=str(path), error=None, finished_at=datetime.now(UTC), duration_s=round(time.perf_counter() - started, 2))
            return "done"
        except Cancelled:
            _set(db, report_id, finished_at=datetime.now(UTC))
            return "cancelled"
        except Exception as exc:  # noqa: BLE001 - любая ошибка генерации: повтор с паузой, затем статус failed
            if self.request.retries < MAX_RETRIES:
                _set(db, report_id, status="queued", error=f"attempt {report.attempts} failed: {type(exc).__name__}: {exc}"[:500], progress=0)
                raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 30))
            _set(db, report_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=datetime.now(UTC))
            return "failed"
