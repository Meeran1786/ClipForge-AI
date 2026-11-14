"""Health, machine facts, dashboard numbers, saved defaults, file picker."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from .. import __version__
from ..context import AppContext, get_ctx
from ..jobs.engine import job_to_dict
from ..media import VIDEO_EXTENSIONS, tool_version
from ..models import Clip, Job, Project, Setting
from ..schemas import CATEGORIES, ProjectOptions, project_to_dict

router = APIRouter(prefix="/api", tags=["system"])

_DEFAULTS_KEY = "project_defaults"


@router.get("/health")
def health() -> dict:
    return {"app": "clipforge", "version": __version__, "ok": True}


@lru_cache(maxsize=8)
def _version_of(exe: str | None) -> str | None:
    return tool_version(exe)


@router.get("/system/info")
def system_info(ctx: AppContext = Depends(get_ctx)) -> dict:
    s = ctx.settings
    usage = shutil.disk_usage(s.data_dir)
    return {
        "version": __version__,
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "ffmpeg": _version_of(s.ffmpeg),
        "ffprobe": _version_of(s.ffprobe),
        "data_dir": str(s.data_dir),
        "disk_free_gb": round(usage.free / 1024**3, 1),
        "disk_total_gb": round(usage.total / 1024**3, 1),
        "gemini_route": s.gemini_route,
        "categories": CATEGORIES,
        "video_extensions": sorted(VIDEO_EXTENSIONS),
    }


@router.get("/stats")
def stats(ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        by_status = dict(s.execute(select(Project.status, func.count(Project.id)).group_by(Project.status)).all())
        clips = dict(s.execute(select(Clip.status, func.count(Clip.id)).group_by(Clip.status)).all())
        jobs = dict(s.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
        active = s.scalars(
            select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.created_at, Job.id)
        ).all()
        recent = s.scalars(
            select(Project).order_by(Project.created_at.desc(), Project.id.desc()).limit(6)
        ).all()
        return {
            "projects_total": sum(by_status.values()),
            "videos_processed": by_status.get("ready", 0),
            "projects_by_status": by_status,
            "clips_total": sum(clips.values()),
            "clips_exported": clips.get("exported", 0),
            "jobs_by_status": jobs,
            "active_jobs": [job_to_dict(j) for j in active],
            "recent_projects": [project_to_dict(p) for p in recent],
        }


@router.get("/settings/defaults")
def get_defaults(ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        row = s.get(Setting, _DEFAULTS_KEY)
    try:
        return ProjectOptions(**(row.value if row else {})).model_dump()
    except ValueError:  # stored value no longer valid: fall back to built-in defaults
        return ProjectOptions().model_dump()


@router.put("/settings/defaults")
def put_defaults(body: ProjectOptions, ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        row = s.get(Setting, _DEFAULTS_KEY)
        if row is None:
            s.add(Setting(key=_DEFAULTS_KEY, value=body.model_dump()))
        else:
            row.value = body.model_dump()
        s.commit()
    return body.model_dump()


_PICKER = r"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    parent=root,
    title="Choose a video",
    filetypes=[("Video files", "{patterns}"), ("All files", "*.*")],
)
root.destroy()
print(path or "", end="")
"""


def pick_video_file() -> str | None:
    """Open the operating system's file dialog on this computer."""
    patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
    proc = subprocess.run(
        [sys.executable, "-c", _PICKER.replace("{patterns}", patterns)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "file dialog failed").strip().splitlines()[-1])
    return proc.stdout.strip() or None


@router.post("/system/pick-file")
def pick_file() -> dict:
    """The app runs on the user's own machine, so the server can show the dialog."""
    try:
        return {"path": pick_video_file()}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500, f"Could not open the file dialog: {exc}") from exc
