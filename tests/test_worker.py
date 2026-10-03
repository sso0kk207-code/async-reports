"""Поведение воркера: идемпотентность, повторные попытки, отмена."""
from app import builders, worker
from app.db import SessionLocal
from app.models import Report
from app.worker import generate_report
from tests.conftest import get_report, make_report


def run(rid: int) -> str:
    return generate_report.apply(args=[rid]).get()


def test_successful_run_updates_status(client):
    rid = make_report()
    assert run(rid) == "done"
    r = get_report(rid)
    assert (r.status, r.progress, r.attempts) == ("done", 100, 1) and r.file_path and r.finished_at


def test_task_is_idempotent_for_duplicate_delivery(client):
    rid = make_report()
    assert run(rid) == "done"
    assert run(rid) == "skipped"  # то же сообщение брокер доставил повторно
    assert get_report(rid).attempts == 1


def test_cancelled_report_is_not_processed(client):
    rid = make_report(status="cancelled")
    assert run(rid) == "skipped"
    assert get_report(rid).file_path is None


def test_flaky_source_succeeds_after_retries(client):
    rid = make_report(kind="flaky_demo")
    run(rid)
    r = get_report(rid)
    assert r.status == "done" and r.attempts == 3 and r.error is None  # две неудачи, третья попытка успешна


def test_permanent_failure_ends_as_failed_with_reason(client, monkeypatch):
    def boom(db, year, path, attempt=1):
        raise RuntimeError("warehouse is down")

    monkeypatch.setitem(builders.BUILDERS, "top_products", boom)
    rid = make_report()
    run(rid)
    r = get_report(rid)
    assert r.status == "failed" and r.attempts == worker.MAX_RETRIES + 1
    assert "RuntimeError: warehouse is down" in r.error and r.file_path is None


def test_cancel_during_work_stops_before_finishing(client, monkeypatch):
    original = builders.BUILDERS["top_products"]

    def cancel_midway(db, year, path, attempt=1):
        with SessionLocal() as other:  # пользователь нажал «отмена» пока воркер строит отчёт
            other.query(Report).update({"status": "cancelled"})
            other.commit()
        return original(db, year, path, attempt)

    monkeypatch.setitem(builders.BUILDERS, "top_products", cancel_midway)
    rid = make_report()
    assert run(rid) == "cancelled"
    r = get_report(rid)
    assert r.status == "cancelled" and r.progress < 100


def test_progress_checkpoints_are_written(client, monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "_set", (lambda orig: lambda db, rid, **v: (seen.append(v.get("progress")), orig(db, rid, **v))[1])(worker._set))
    run(make_report())
    assert [p for p in seen if p is not None] == [10, 35, 85, 100]


def test_celery_is_configured_for_reliability():
    conf = worker.celery_app.conf
    assert conf.task_acks_late is True and conf.worker_prefetch_multiplier == 1
