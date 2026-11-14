"""Projects: one long source video each."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..context import AppContext, get_ctx
from ..ingest import detect_platform
from ..media import VIDEO_EXTENSIONS
from ..models import Clip, Job, Project
from ..pipelines import default_link_title
from ..rights import download_blocker, write_rights_file
from ..schemas import AttachFile, ProjectCreate, RightsUpdate, project_to_dict

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _clip_counts(s, project_ids: list[int]) -> dict[int, int]:
    if not project_ids:
        return {}
    rows = s.execute(
        select(Clip.project_id, func.count(Clip.id))
        .where(Clip.project_id.in_(project_ids))
        .group_by(Clip.project_id)
    ).all()
    return {pid: n for pid, n in rows}


def _video_path(raw: str | None) -> Path:
    cleaned = (raw or "").strip().strip('"')
    if not cleaned:
        raise HTTPException(422, "Choose a video file")
    path = Path(cleaned)
    if not path.is_file():
        raise HTTPException(422, f"File not found: {cleaned}")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(
            422, f"'{path.suffix}' is not a supported video type ({', '.join(sorted(VIDEO_EXTENSIONS))})"
        )
    return path.resolve()


def _busy(s, project_id: int) -> bool:
    return bool(
        s.scalar(
            select(func.count(Job.id)).where(
                Job.project_id == project_id, Job.status.in_(("queued", "running"))
            )
        )
    )


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.get("")
def list_projects(ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    with ctx.session() as s:
        projects = s.scalars(select(Project).order_by(Project.created_at.desc(), Project.id.desc())).all()
        counts = _clip_counts(s, [p.id for p in projects])
        return [project_to_dict(p, clip_count=counts.get(p.id, 0)) for p in projects]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, ctx: AppContext = Depends(get_ctx)) -> dict:
    common = dict(
        creator=_clean(body.creator),
        rights_status=body.rights_status,
        rights_note=_clean(body.rights_note),
        options=body.options.model_dump(),
    )

    if body.source == "local":
        path = _video_path(body.path)
        project = Project(
            title=_clean(body.title) or path.stem,
            source_kind="local",
            source_path=str(path),
            status="processing",
            **common,
        )
        pipeline = "ingest_local"
    else:
        url = (body.url or "").strip()
        platform = detect_platform(url)
        if platform is None:
            raise HTTPException(
                422, "Paste a YouTube, Twitch or Kick video link. For anything else, use a local file."
            )
        blocker = download_blocker(body.rights_status, common["creator"], common["rights_note"])
        project = Project(
            title=_clean(body.title) or default_link_title(platform),
            source_kind=platform,
            source_url=url,
            status="needs_rights" if blocker else "processing",
            status_detail=blocker,
            **common,
        )
        pipeline = None if blocker else "ingest_url"

    with ctx.session() as s:
        s.add(project)
        s.commit()
        write_rights_file(ctx.settings, project)
        data = project_to_dict(project)

    if pipeline:
        ctx.worker.enqueue(pipeline, project_id=project.id)
    ctx.bus.publish({"type": "project", "id": project.id})
    return data


@router.get("/{project_id}")
def get_project(project_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        counts = _clip_counts(s, [project_id])
        return project_to_dict(project, clip_count=counts.get(project_id, 0))


@router.patch("/{project_id}/rights")
def update_rights(project_id: int, body: RightsUpdate, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change the rights record. A link that was waiting on it starts downloading."""
    start = False
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        project.rights_status = body.rights_status
        project.creator = _clean(body.creator)
        project.rights_note = _clean(body.rights_note)

        waiting_link = project.source_kind != "local" and not project.source_path
        if waiting_link and not _busy(s, project_id):
            blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
            if blocker:
                project.status, project.status_detail = "needs_rights", blocker
            elif project.status == "needs_rights":
                project.status, project.status_detail = "processing", None
                start = True
        s.commit()
        write_rights_file(ctx.settings, project)
        data = project_to_dict(project, clip_count=_clip_counts(s, [project_id]).get(project_id, 0))

    if start:
        ctx.worker.enqueue("ingest_url", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


@router.post("/{project_id}/attach")
def attach_file(project_id: int, body: AttachFile, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Give a link project a video file the user supplies, instead of downloading."""
    path = _video_path(body.path)
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        project.source_path = str(path)
        project.media = None
        project.thumbnail = None
        project.proxy_path = None
        project.audio_path = None
        project.status, project.status_detail = "processing", None
        s.commit()
        data = project_to_dict(project)
    ctx.worker.enqueue("ingest_local", project_id=project_id)
    ctx.bus.publish({"type": "project", "id": project_id})
    return data


def _project_file(ctx: AppContext, project_id: int, attr: str) -> Path:
    with ctx.session() as s:
        project = s.get(Project, project_id)
        value = getattr(project, attr) if project else None
    if not value or not Path(value).is_file():
        raise HTTPException(404, "Not available")
    return Path(value)


@router.get("/{project_id}/thumbnail")
def get_thumbnail(project_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    path = _project_file(ctx, project_id, "thumbnail")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/{project_id}/proxy")
def get_proxy(project_id: int, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The 360p preview copy. Supports byte ranges, so the player can seek."""
    path = _project_file(ctx, project_id, "proxy_path")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, ctx: AppContext = Depends(get_ctx)) -> None:
    """Remove the project and the app's own files for it (downloads, preview copy, audio).

    A video file the user pointed to on their own disk is never touched.
    """
    with ctx.session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if _busy(s, project_id):
            raise HTTPException(409, "This project is still processing. Cancel its job first.")
        s.delete(project)
        s.commit()
    shutil.rmtree(ctx.settings.project_dir(project_id), ignore_errors=True)
    ctx.bus.publish({"type": "project", "id": project_id})
