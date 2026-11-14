"""Background jobs: list, inspect, cancel, retry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..context import AppContext, get_ctx
from ..jobs.engine import job_to_dict
from ..models import Job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class SelfTestRequest(BaseModel):
    stage_seconds: float = Field(default=6, ge=1, le=120)


@router.get("")
def list_jobs(
    project_id: int | None = None, limit: int = 50, ctx: AppContext = Depends(get_ctx)
) -> list[dict]:
    with ctx.session() as s:
        query = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(max(1, min(limit, 200)))
        if project_id is not None:
            query = query.where(Job.project_id == project_id)
        return [job_to_dict(j) for j in s.scalars(query).all()]


@router.post("/selftest", status_code=201)
def start_selftest(body: SelfTestRequest | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        running = s.scalars(
            select(Job).where(Job.kind == "selftest", Job.status.in_(("queued", "running")))
        ).first()
        if running is not None:
            raise HTTPException(409, "A self-test is already running")
    params = (body or SelfTestRequest()).model_dump()
    return ctx.worker.enqueue("selftest", params=params)


@router.get("/{job_id}")
def get_job(job_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job_to_dict(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    data = ctx.worker.cancel(job_id)
    if data is None:
        raise HTTPException(404, "Job not found")
    return data


@router.post("/{job_id}/retry")
def retry_job(job_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    data = ctx.worker.retry(job_id)
    if data is None:
        raise HTTPException(404, "Job not found")
    return data
