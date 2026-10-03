import html
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.builders import MONTHS, monthly_matrix, top_products
from app.db import Base, engine, get_db
from app.models import Report
from app.seed import ensure_seeded
from app.worker import generate_report


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    ensure_seeded()
    yield


app = FastAPI(
    title="Async Reports API",
    version="1.0.0",
    description="Тяжёлые отчёты в фоне: API отвечает сразу, Celery-воркер строит Excel, клиент следит за прогрессом и скачивает файл.",
    lifespan=lifespan,
)

Kind = Literal["sales_by_month", "top_products", "flaky_demo"]


class ReportIn(BaseModel):
    kind: Kind
    year: int = Field(default=2025, ge=2000, le=2100)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    year: int
    status: str
    progress: int
    attempts: int
    rows_processed: int | None
    error: str | None
    duration_s: float | None
    created_at: datetime
    finished_at: datetime | None


@app.get("/health", tags=["service"])
def health():
    return {"status": "ok"}


@app.post("/reports", response_model=ReportOut, status_code=202, tags=["reports"], summary="Заказать отчёт (ответ мгновенный, работа в фоне)")
def order_report(data: ReportIn, response: Response, db: Session = Depends(get_db)):
    # Тот же отчёт уже строится: отдаём его, а не плодим дубли тяжёлой работы
    active = db.scalar(select(Report).where(Report.kind == data.kind, Report.year == data.year, Report.status.in_(["queued", "running"])))
    if active:
        response.status_code = 200
        return active
    report = Report(kind=data.kind, year=data.year, status="queued", progress=0, attempts=0)
    db.add(report)
    db.commit()
    generate_report.delay(report.id)
    db.refresh(report)  # в eager-режиме (тесты) задача уже выполнилась
    return report


def _get(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@app.get("/reports", response_model=list[ReportOut], tags=["reports"])
def list_reports(db: Session = Depends(get_db)):
    return db.scalars(select(Report).order_by(Report.id.desc()).limit(50)).all()


@app.get("/reports/{report_id}", response_model=ReportOut, tags=["reports"], summary="Статус и прогресс")
def report_status(report_id: int, db: Session = Depends(get_db)):
    return _get(db, report_id)


@app.get("/reports/{report_id}/download", tags=["reports"], summary="Скачать готовый Excel")
def download(report_id: int, db: Session = Depends(get_db)):
    report = _get(db, report_id)
    if report.status != "done" or not report.file_path or not Path(report.file_path).exists():
        raise HTTPException(409, f"Report is not ready (status: {report.status})")
    return FileResponse(report.file_path, filename=Path(report.file_path).name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.delete("/reports/{report_id}", status_code=204, tags=["reports"], summary="Отменить или удалить отчёт")
def cancel_or_delete(report_id: int, db: Session = Depends(get_db)):
    report = _get(db, report_id)
    if report.status in ("queued", "running"):
        report.status = "cancelled"  # воркер увидит это на ближайшей контрольной точке
    else:
        if report.file_path:
            Path(report.file_path).unlink(missing_ok=True)
        db.delete(report)
    db.commit()


def _table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{tr}</table>"


@app.get("/reports/{report_id}/preview", response_class=HTMLResponse, tags=["reports"], summary="Предпросмотр отчёта в браузере")
def preview(report_id: int, db: Session = Depends(get_db)):
    r = _get(db, report_id)
    if r.status != "done":
        raise HTTPException(409, f"Report is not ready (status: {r.status})")
    money = lambda cents: f"{cents / 100:,.2f}".replace(",", " ")  # noqa: E731
    if r.kind == "sales_by_month":
        regions, matrix, processed = monthly_matrix(db, r.year)
        rows = [[MONTHS[i], *[money(v) for v in row], money(sum(row))] for i, row in enumerate(matrix)]
        rows.append(["Всего", *[money(sum(m[j] for m in matrix)) for j in range(len(regions))], money(sum(map(sum, matrix)))])
        body, title = _table(["Месяц", *regions, "Итого"], rows), f"Выручка по месяцам и регионам, {r.year}"
    else:
        data, processed = top_products(db, r.year)
        body, title = _table(["#", "Товар", "Выручка", "Продаж"], [[str(i), n, money(v), str(c)] for i, (n, v, c) in enumerate(data, 1)]), f"Топ товаров, {r.year}"
    meta = f"Отчёт №{r.id} · обработано строк: {processed:,} · построен за {r.duration_s} с · попыток: {r.attempts}".replace(",", " ")
    return PREVIEW.replace("{title}", html.escape(title)).replace("{meta}", meta).replace("{body}", body)


PREVIEW = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Отчёт</title><style>
body{margin:0;background:#0b1020;color:#e8ecf5;font:15px/1.45 'Segoe UI',Arial,sans-serif;padding:30px 40px}h1{margin:0 0 4px;font-size:26px}.m{color:#8f9bc0;margin-bottom:18px}
table{border-collapse:collapse;background:#141b34;border:1px solid #26305a;border-radius:12px;overflow:hidden}th,td{padding:8px 16px;text-align:right;border-bottom:1px solid #26305a}
th{background:#1f2a5a;color:#fff;font-size:13px}td:first-child,th:first-child{text-align:left}tr:last-child td{font-weight:700;color:#fbbf24}</style></head>
<body><h1>{title}</h1><div class="m">{meta}</div>{body}</body></html>"""
