"""Health, machine facts, dashboard numbers, saved defaults, file picker."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from .. import __version__
from ..captions import FONTS, PRESET_LABELS, PRESETS
from ..context import AppContext, get_ctx
from ..exports import export_folder
from ..jobs.engine import job_to_dict
from ..media import VIDEO_EXTENSIONS, tool_version
from ..models import Clip, Job, Moment, Project, Setting
from ..moments import (
    DEFAULT_WEIGHTS,
    SCORE_KEYS,
    SCORE_NOTE,
    SCORE_SOURCE,
    SUBSCORE_MEANING,
    clean_weights,
    viral_score,
)
from ..music import AUDIO_EXTENSIONS
from ..pipelines import WEIGHTS_KEY, load_weights, reselect
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
        "fonts_installed": sum((s.fonts_dir / f).is_file() for f in FONTS.values()),
        "fonts_total": len(FONTS),
        "data_dir": str(s.data_dir),
        "disk_free_gb": round(usage.free / 1024**3, 1),
        "disk_total_gb": round(usage.total / 1024**3, 1),
        "gemini_route": s.gemini_route,
        "ai_models": s.agy_models if s.gemini_route != "api" else s.gemini_model,
        "speech_model": s.whisper_model,
        "categories": CATEGORIES,
        "video_extensions": sorted(VIDEO_EXTENSIONS),
    }


@router.get("/stats")
def stats(ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        by_status = dict(s.execute(select(Project.status, func.count(Project.id)).group_by(Project.status)).all())
        # only clips that passed their checks count as "generated"
        clips = dict(
            s.execute(
                select(Clip.status, func.count(Clip.id)).where(Clip.valid.is_(True)).group_by(Clip.status)
            ).all()
        )
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


def _weights_view(weights: dict[str, float]) -> dict:
    return {"weights": weights, "defaults": DEFAULT_WEIGHTS, "meaning": SUBSCORE_MEANING,
            "source": SCORE_SOURCE, "note": SCORE_NOTE}


@router.get("/settings/weights")
def get_weights(ctx: AppContext = Depends(get_ctx)) -> dict:
    with ctx.session() as s:
        return _weights_view(load_weights(s))


@router.put("/settings/weights")
def put_weights(body: dict[str, float], ctx: AppContext = Depends(get_ctx)) -> dict:
    """Change how the scores add up. Every stored moment is re-scored at once, without the AI."""
    unknown = [k for k in body if k not in SCORE_KEYS]
    if unknown:
        raise HTTPException(422, f"Unknown score parts: {', '.join(unknown)}")
    if any(not isinstance(v, (int, float)) or v < 0 for v in body.values()) or sum(body.values()) <= 0:
        raise HTTPException(422, "Weights must be zero or more, and not all zero")
    # The body is the whole set: a part that is left out counts for nothing.
    weights = clean_weights({k: body.get(k, 0) for k in SCORE_KEYS})
    changed: list[int] = []
    with ctx.session() as s:
        row = s.get(Setting, WEIGHTS_KEY)
        if row is None:
            s.add(Setting(key=WEIGHTS_KEY, value=weights))
        else:
            row.value = weights
        for moment in s.scalars(select(Moment)).all():
            moment.score = viral_score(moment.subscores or {}, weights, moment.measured)
        s.flush()
        for project in s.scalars(select(Project)).all():
            if (project.analysis or {}).get("moments"):
                reselect(s, project)
                changed.append(project.id)
        s.commit()
    for project_id in changed:
        ctx.bus.publish({"type": "project", "id": project_id})
    return _weights_view(weights)


@router.get("/captions/presets")
def caption_presets() -> dict:
    return {
        "presets": [{"name": name, "label": PRESET_LABELS[name], **values} for name, values in PRESETS.items()],
        "fonts": [{"name": name, "file": file} for name, file in FONTS.items()],
    }


@router.get("/fonts/{name}")
def font_file(name: str, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    """The caption fonts, so the dashboard can show a style sample in the real typeface."""
    if name not in FONTS.values():
        raise HTTPException(404, "Unknown font")
    path = ctx.settings.fonts_dir / name
    if not path.is_file():
        raise HTTPException(404, "Font not installed (expected in tools\\fonts)")
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "max-age=86400"})


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


_AUDIO_PICKER = _PICKER.replace("Choose a video", "Choose a music track").replace('"Video files"', '"Audio files"')


def pick_audio_file() -> str | None:
    """Open the operating system's file dialog for an audio file."""
    patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
    proc = subprocess.run(
        [sys.executable, "-c", _AUDIO_PICKER.replace("{patterns}", patterns)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "file dialog failed").strip().splitlines()[-1])
    return proc.stdout.strip() or None


@router.post("/system/pick-audio")
def pick_audio() -> dict:
    try:
        return {"path": pick_audio_file()}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500, f"Could not open the file dialog: {exc}") from exc


def open_in_explorer(folder: Path) -> None:
    """Show a folder in the file manager on this computer."""
    if sys.platform == "win32":
        os.startfile(str(folder))  # noqa: S606 - the owner's own folder, on their own machine
    else:
        subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", str(folder)])


@router.post("/system/open-exports")
def open_exports(project_id: int | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Open the export folder (a project's own, when given). Only folders inside it can be opened."""
    root = ctx.settings.exports_dir
    folder = root
    if project_id is not None:
        with ctx.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise HTTPException(404, "Project not found")
            folder = export_folder(root, project)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        open_in_explorer(folder)
    except OSError as exc:
        raise HTTPException(500, f"Could not open the folder: {exc}") from exc
    return {"folder": str(folder)}


@router.post("/system/pick-file")
def pick_file() -> dict:
    """The app runs on the user's own machine, so the server can show the dialog."""
    try:
        return {"path": pick_video_file()}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(500, f"Could not open the file dialog: {exc}") from exc
