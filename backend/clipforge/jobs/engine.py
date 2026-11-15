"""SQLite-backed job queue with resumable stages.

A job is an ordered list of stages. Each stage's status and result are stored
as soon as it finishes, so a job that is interrupted (server stopped or killed)
continues from the first unfinished stage on the next start.

One worker thread runs one job at a time: this machine has four cores and the
stages themselves (FFmpeg, analysis) use them.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..events import EventBus
from ..models import Job, JobStage, iso, utcnow

log = logging.getLogger("clipforge.jobs")

RETRY_DELAY_SECONDS = 1.0
PUBLISH_INTERVAL = 0.2
DB_WRITE_INTERVAL = 1.0


class JobCancelled(Exception):
    """The user cancelled the job."""


class WorkerStopping(Exception):
    """The server is shutting down; the job goes back to the queue."""


@dataclass(frozen=True)
class StageDef:
    name: str
    label: str
    fn: Callable[["StageContext"], dict[str, Any] | None]
    max_attempts: int = 2


@dataclass(frozen=True)
class Pipeline:
    stages: list[StageDef]
    # Called once when the job ends (done, failed or cancelled), inside a session.
    on_finish: Callable[[Session, Job], None] | None = None
    # Called when a failed or cancelled job is put back in the queue.
    on_retry: Callable[[Session, Job], None] | None = None
    stage_names: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_names", [s.name for s in self.stages])


class StageContext:
    """What a stage function gets to work with."""

    def __init__(
        self,
        worker: "JobWorker",
        job_id: int,
        project_id: int | None,
        params: dict[str, Any],
        results: dict[str, dict[str, Any]],
        stage_name: str,
    ) -> None:
        self._worker = worker
        self.job_id = job_id
        self.project_id = project_id
        self.params = params
        self.results = results  # results of earlier stages, by stage name
        self.stage_name = stage_name
        self.settings: Settings = worker.settings
        self.session: sessionmaker[Session] = worker.session

    def report(self, progress: float, message: str | None = None) -> None:
        """Report stage progress (0..1). Also the cancellation checkpoint."""
        self.check()
        self._worker._report(self.job_id, self.stage_name, progress, message)

    def check(self) -> None:
        self._worker._check(self.job_id)

    def sleep(self, seconds: float) -> None:
        """Sleep that wakes up early for cancel or shutdown."""
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    def publish_project(self) -> None:
        if self.project_id is not None:
            self._worker.bus.publish({"type": "project", "id": self.project_id})


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "project_id": job.project_id,
        "progress": round(job.progress or 0.0, 4),
        "error": job.error,
        "params": dict(job.params or {}),
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
        "stages": [
            {
                "name": st.name,
                "label": st.label,
                "status": st.status,
                "progress": round(st.progress or 0.0, 4),
                "message": st.message,
                "attempts": st.attempts,
            }
            for st in sorted(job.stages, key=lambda s: s.position)
        ],
    }


def _overall(stages: list[dict[str, Any]]) -> float:
    if not stages:
        return 0.0
    total = 0.0
    for st in stages:
        if st["status"] == "done":
            total += 1.0
        elif st["status"] == "running":
            total += max(0.0, min(1.0, st["progress"]))
    return total / len(stages)


_KEEP = object()


class JobWorker:
    def __init__(
        self,
        settings: Settings,
        session: sessionmaker[Session],
        bus: EventBus,
        pipelines: dict[str, Pipeline],
    ) -> None:
        self.settings = settings
        self.session = session
        self.bus = bus
        self.pipelines = pipelines
        self._cancel: set[int] = set()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] | None = None
        self._last_pub = 0.0
        self._last_db = 0.0

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        resumed = self.recover()
        if resumed:
            log.info("Resuming %d interrupted job(s)", resumed)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clipforge-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def recover(self) -> int:
        """Put jobs that were mid-run when the process died back in the queue."""
        with self.session() as s:
            jobs = s.scalars(select(Job).where(Job.status == "running")).all()
            for job in jobs:
                job.status = "queued"
                for st in job.stages:
                    if st.status == "running":
                        st.status = "pending"
                        st.progress = 0.0
                        st.message = "Restarted after the app was interrupted"
            s.commit()
            return len(jobs)

    # ---- public API (called from request threads) -------------------------

    def enqueue(
        self, kind: str, project_id: int | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        pipeline = self.pipelines[kind]
        with self.session() as s:
            job = Job(kind=kind, project_id=project_id, params=params or {}, status="queued")
            job.stages = [
                JobStage(position=i, name=d.name, label=d.label)
                for i, d in enumerate(pipeline.stages)
            ]
            s.add(job)
            s.commit()
            data = job_to_dict(job)
        self.bus.publish({"type": "job", "job": data})
        self._wake.set()
        return data

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        with self.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                self._run_on_finish(s, job)
                s.commit()
            elif job.status == "running":
                self._cancel.add(job_id)  # picked up at the stage's next checkpoint
            data = job_to_dict(job)
        self.bus.publish({"type": "job", "job": data})
        return data

    def retry(self, job_id: int) -> dict[str, Any] | None:
        with self.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            if job.status in ("failed", "cancelled"):
                for st in job.stages:
                    if st.status != "done":
                        st.status = "pending"
                        st.progress = 0.0
                        st.message = None
                        st.attempts = 0
                job.status = "queued"
                job.error = None
                pipeline = self.pipelines.get(job.kind)
                if pipeline and pipeline.on_retry:
                    pipeline.on_retry(s, job)
                s.commit()
            data = job_to_dict(job)
            project_id = job.project_id
        self.bus.publish({"type": "job", "job": data})
        if project_id is not None:
            self.bus.publish({"type": "project", "id": project_id})
        self._wake.set()
        return data

    # ---- worker loop -------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._claim()
            if job_id is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                self._run(job_id)
            except WorkerStopping:
                log.info("Job %d paused for shutdown; it resumes on next start", job_id)
                break
            except Exception:  # a bug in the engine itself, not in a stage
                log.exception("Job %d crashed the worker loop", job_id)
                self._finish_job(job_id, "failed", "Internal error; see the log file")
            finally:
                self._snapshot = None

    def _claim(self) -> int | None:
        with self.session() as s:
            job = s.scalars(
                select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(1)
            ).first()
            if job is None:
                return None
            job.status = "running"
            s.commit()
            self._snapshot = job_to_dict(job)
            return job.id

    def _run(self, job_id: int) -> None:
        with self.session() as s:
            job = s.get(Job, job_id)
            assert job is not None
            kind, project_id, params = job.kind, job.project_id, dict(job.params or {})
            order = [st.name for st in job.stages]
            results = {st.name: (st.result or {}) for st in job.stages if st.status == "done"}
        self.bus.publish({"type": "job", "job": copy.deepcopy(self._snapshot)})

        pipeline = self.pipelines.get(kind)
        if pipeline is None:
            self._finish_job(job_id, "failed", f"Unknown job kind: {kind}")
            return
        defs = {d.name: d for d in pipeline.stages}

        for name in order:
            if name in results:
                continue  # finished before an earlier interruption
            stage_def = defs.get(name)
            if stage_def is None:
                self._finish_job(job_id, "failed", f"Unknown stage: {name}")
                return
            if not self._run_stage(job_id, project_id, params, results, stage_def):
                return
        self._finish_job(job_id, "done", None)

    def _run_stage(
        self,
        job_id: int,
        project_id: int | None,
        params: dict[str, Any],
        results: dict[str, dict[str, Any]],
        d: StageDef,
    ) -> bool:
        ctx = StageContext(self, job_id, project_id, params, results, d.name)
        last_error: Exception | None = None
        for attempt in range(1, d.max_attempts + 1):
            try:
                if attempt > 1:
                    ctx.sleep(RETRY_DELAY_SECONDS)
                self._update_stage(
                    job_id,
                    d.name,
                    status="running",
                    progress=0.0,
                    message=None if attempt == 1 else f"Retrying (attempt {attempt})",
                    attempts=attempt,
                    started=True,
                )
                ctx.check()
                result = d.fn(ctx) or {}
            except JobCancelled:
                self._cancel.discard(job_id)
                self._update_stage(job_id, d.name, status="pending", progress=0.0, message="Cancelled")
                self._finish_job(job_id, "cancelled", None)
                return False
            except WorkerStopping:
                self._requeue(job_id, d.name)
                raise
            except Exception as exc:
                last_error = exc
                if getattr(exc, "no_retry", False):  # a refusal: trying again cannot help
                    log.warning("Job %d stage %s refused: %s", job_id, d.name, exc)
                    break
                log.exception("Job %d stage %s attempt %d failed", job_id, d.name, attempt)
                continue
            else:
                self._update_stage(
                    job_id, d.name, status="done", progress=1.0, message=_KEEP,
                    result=result, finished=True,
                )
                results[d.name] = result
                return True

        self._update_stage(job_id, d.name, status="failed", message=str(last_error), finished=True)
        self._finish_job(job_id, "failed", f"{d.label}: {last_error}")
        return False

    # ---- state changes -----------------------------------------------------

    def _check(self, job_id: int) -> None:
        if self._stop.is_set():
            raise WorkerStopping()
        if job_id in self._cancel:
            raise JobCancelled()

    def _update_stage(
        self,
        job_id: int,
        name: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        message: Any = _KEEP,
        attempts: int | None = None,
        result: dict[str, Any] | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        with self.session() as s:
            job = s.get(Job, job_id)
            assert job is not None
            st = next(x for x in job.stages if x.name == name)
            if status is not None:
                st.status = status
            if progress is not None:
                st.progress = progress
            if message is not _KEEP:
                st.message = message
            if attempts is not None:
                st.attempts = attempts
            if result is not None:
                st.result = result
            if started:
                st.started_at = utcnow()
                st.finished_at = None
            if finished:
                st.finished_at = utcnow()
            data = job_to_dict(job)
            job.progress = _overall(data["stages"])
            job.updated_at = utcnow()
            s.commit()
            data = job_to_dict(job)
        self._snapshot = data
        self._last_pub = self._last_db = time.monotonic()
        self.bus.publish({"type": "job", "job": copy.deepcopy(data)})

    def _report(self, job_id: int, name: str, progress: float, message: str | None) -> None:
        progress = max(0.0, min(1.0, float(progress)))
        final = progress >= 1.0  # a stage's closing summary is never dropped by throttling
        now = time.monotonic()
        snap = self._snapshot
        if snap is not None and snap["id"] == job_id:
            for st in snap["stages"]:
                if st["name"] == name:
                    st["progress"] = round(progress, 4)
                    if message is not None:
                        st["message"] = message
            snap["progress"] = round(_overall(snap["stages"]), 4)
            if final or now - self._last_pub >= PUBLISH_INTERVAL:
                self._last_pub = now
                self.bus.publish({"type": "job", "job": copy.deepcopy(snap)})
        if final or now - self._last_db >= DB_WRITE_INTERVAL:
            self._last_db = now
            with self.session() as s:
                job = s.get(Job, job_id)
                if job is None:
                    return
                for st in job.stages:
                    if st.name == name:
                        st.progress = progress
                        if message is not None:
                            st.message = message
                job.progress = _overall(job_to_dict(job)["stages"])
                s.commit()

    def _requeue(self, job_id: int, stage_name: str) -> None:
        with self.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = "queued"
            for st in job.stages:
                if st.name == stage_name and st.status == "running":
                    st.status = "pending"
                    st.progress = 0.0
                    st.message = "Paused when the app closed"
            s.commit()

    def _run_on_finish(self, s: Session, job: Job) -> None:
        pipeline = self.pipelines.get(job.kind)
        if pipeline and pipeline.on_finish:
            try:
                pipeline.on_finish(s, job)
            except Exception:
                log.exception("on_finish hook failed for job %d", job.id)

    def _finish_job(self, job_id: int, status: str, error: str | None) -> None:
        with self.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.error = error
            if status == "done":
                job.progress = 1.0
            job.updated_at = utcnow()
            self._run_on_finish(s, job)
            s.commit()
            data = job_to_dict(job)
            project_id = job.project_id
        log.info("Job %d (%s) finished: %s%s", job_id, data["kind"], status, f" - {error}" if error else "")
        self.bus.publish({"type": "job", "job": data})
        if project_id is not None:
            self.bus.publish({"type": "project", "id": project_id})
