"""Job engine: ordering, resume, retry, cancel."""

from __future__ import annotations

import threading

import pytest

from clipforge.jobs import JobWorker, Pipeline, StageDef
from clipforge.models import Job

from .conftest import wait_for


class Recorder:
    """Stage functions that record how often each one ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.block = threading.Event()  # set = stages may finish
        self.block.set()
        self.entered = threading.Event()

    def stage(self, name: str, slow: bool = False):
        def fn(ctx):
            self.calls.append(name)
            if name in self.fail:
                raise RuntimeError(f"{name} broke")
            if slow:
                self.entered.set()
                while not self.block.is_set():
                    ctx.sleep(0.01)
                    ctx.report(0.5, "waiting")
            return {"ran": name}

        return fn


def make_worker(settings, db, bus, rec: Recorder, slow: str | None = None) -> JobWorker:
    pipeline = Pipeline(
        stages=[StageDef(n, n.upper(), rec.stage(n, slow=(n == slow))) for n in ("a", "b", "c")]
    )
    return JobWorker(settings, db, bus, {"demo": pipeline})


def status_of(db, job_id: int) -> str:
    with db() as s:
        return s.get(Job, job_id).status


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


def test_runs_every_stage_in_order(settings, db, bus, rec):
    worker = make_worker(settings, db, bus, rec)
    worker.start()
    try:
        job = worker.enqueue("demo")
        wait_for(lambda: status_of(db, job["id"]) == "done")
    finally:
        worker.stop()
    assert rec.calls == ["a", "b", "c"]
    with db() as s:
        row = s.get(Job, job["id"])
        assert row.progress == 1.0
        assert [st.status for st in row.stages] == ["done"] * 3
        assert row.stages[0].result == {"ran": "a"}


def test_resumes_after_a_crash_without_redoing_finished_stages(settings, db, bus, rec):
    worker = make_worker(settings, db, bus, rec)
    job = worker.enqueue("demo")  # worker not started: nothing runs yet

    # Simulate a process that died while stage "b" was running.
    with db() as s:
        row = s.get(Job, job["id"])
        row.status = "running"
        row.stages[0].status = "done"
        row.stages[0].result = {"ran": "a"}
        row.stages[1].status = "running"
        row.stages[1].progress = 0.4
        s.commit()

    worker.start()  # a fresh start recovers the interrupted job
    try:
        wait_for(lambda: status_of(db, job["id"]) == "done")
    finally:
        worker.stop()
    assert rec.calls == ["b", "c"], "stage a was already finished and must not run again"


def test_failed_stage_is_retried_then_job_fails_and_manual_retry_resumes(settings, db, bus, rec):
    rec.fail.add("b")
    worker = make_worker(settings, db, bus, rec)
    worker.start()
    try:
        job = worker.enqueue("demo")
        wait_for(lambda: status_of(db, job["id"]) == "failed")
        assert rec.calls == ["a", "b", "b"], "stage b gets two attempts"
        with db() as s:
            row = s.get(Job, job["id"])
            assert "b broke" in row.error
            assert row.stages[1].status == "failed"
            assert row.stages[2].status == "pending"

        rec.fail.clear()
        worker.retry(job["id"])
        wait_for(lambda: status_of(db, job["id"]) == "done")
    finally:
        worker.stop()
    assert rec.calls == ["a", "b", "b", "b", "c"], "retry continues from the failed stage"


def test_cancel_a_running_job(settings, db, bus, rec):
    rec.block.clear()
    worker = make_worker(settings, db, bus, rec, slow="b")
    worker.start()
    try:
        job = worker.enqueue("demo")
        assert rec.entered.wait(10)
        worker.cancel(job["id"])
        wait_for(lambda: status_of(db, job["id"]) == "cancelled")
    finally:
        worker.stop()
    assert rec.calls == ["a", "b"]


def test_cancel_a_queued_job(settings, db, bus, rec):
    worker = make_worker(settings, db, bus, rec)
    job = worker.enqueue("demo")
    assert worker.cancel(job["id"])["status"] == "cancelled"
    assert rec.calls == []


def test_graceful_stop_puts_the_job_back_in_the_queue(settings, db, bus, rec):
    rec.block.clear()
    worker = make_worker(settings, db, bus, rec, slow="b")
    worker.start()
    job = worker.enqueue("demo")
    assert rec.entered.wait(10)
    worker.stop()
    assert status_of(db, job["id"]) == "queued"

    rec.block.set()
    worker.start()
    try:
        wait_for(lambda: status_of(db, job["id"]) == "done")
    finally:
        worker.stop()
    assert rec.calls == ["a", "b", "b", "c"]
