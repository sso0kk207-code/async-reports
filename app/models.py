from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (Index("ix_sales_date", "sold_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sold_on: Mapped[date] = mapped_column(Date)
    region: Mapped[str] = mapped_column(String(32))
    product: Mapped[str] = mapped_column(String(64))
    amount_cents: Mapped[int] = mapped_column(Integer)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | done | failed | cancelled
    progress: Mapped[int] = mapped_column(Integer, default=0)
    rows_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
