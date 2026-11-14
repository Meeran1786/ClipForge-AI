"""Job pipelines.

`ingest_local` - a video file on this computer: read its facts, grab a poster
                 frame, make a 360p preview copy, extract 16 kHz mono audio.
`ingest_url`   - a link: look the video up, download it (only with a rights
                 record), then the same four stages.
`selftest`     - checks the machine can do what the app needs: FFmpeg present, a
             captioned vertical render works, storage is writable. Its last two
             stages only wait, so progress, cancel and resume-after-kill can be
             seen without a real video.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from .ingest import PLATFORM_LABELS, IngestRefused, source_for
from .jobs.engine import Pipeline, StageContext, StageDef
from .media import (
    MediaError,
    extract_audio,
    make_proxy,
    make_thumbnail,
    probe_media,
    run,
    tool_version,
)
from .models import Job, Project
from .rights import download_blocker, write_rights_file


def default_link_title(kind: str) -> str:
    """Placeholder name for a link project until the platform tells us the real title."""
    return f"{PLATFORM_LABELS.get(kind, kind.capitalize())} video"

# ---- ingest -----------------------------------------------------------------

MIN_FREE_BYTES = 2 * 1024**3  # keep this much free after a download


def _source_file(ctx: StageContext) -> Path:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        if project is None or not project.source_path:
            raise MediaError("This project has no video file yet")
        path = Path(project.source_path)
    if not path.is_file():
        raise MediaError(f"The video file is missing: {path}")
    return path


def _media_info(ctx: StageContext) -> dict:
    info = ctx.results.get("probe")
    if info:
        return info
    with ctx.session() as s:
        return dict(s.get(Project, ctx.project_id).media or {})


def _metadata(ctx: StageContext) -> dict:
    """Look the link up on its platform. Nothing is downloaded here."""
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        url, kind = project.source_url, project.source_kind
        blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
    # The same check runs when the project is created; it is repeated here so no
    # code path can reach the download without a rights record.
    if blocker:
        raise IngestRefused(blocker, "rights", hint=False)
    source = source_for(url or "")
    if source is None:
        raise IngestRefused("This link is not from a supported platform.", "unsupported")

    ctx.report(0.1, f"Asking {source.label} about this video")
    meta = source.fetch_metadata(url, ctx.settings)

    free = shutil.disk_usage(ctx.settings.data_dir).free
    need = int((meta.est_bytes or 0) * 1.4) + MIN_FREE_BYTES
    if free < need:
        raise IngestRefused(
            f"Not enough disk space: this video needs about {need / 1024**3:.1f} GB free "
            f"and the data drive has {free / 1024**3:.1f} GB.",
            "disk", hint=False,
        )

    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.source_meta = meta.to_dict()
        if meta.title and project.title == default_link_title(kind):
            project.title = meta.title[:300]
        s.commit()
    ctx.publish_project()
    return meta.to_dict()


def _download(ctx: StageContext) -> dict:
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        url = project.source_url
        blocker = download_blocker(project.rights_status, project.creator, project.rights_note)
    if blocker:
        raise IngestRefused(blocker, "rights", hint=False)
    source = source_for(url or "")
    if source is None:
        raise IngestRefused("This link is not from a supported platform.", "unsupported")

    dest_dir = ctx.settings.project_dir(ctx.project_id)
    path = source.download(url, dest_dir, ctx.settings, ctx.report)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.source_path = str(path)
        s.commit()
    ctx.publish_project()
    return {"path": str(path), "bytes": path.stat().st_size}


def _probe(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    ctx.report(0.1, "Reading video information")
    info = probe_media(ctx.settings, path)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.media = info
        s.commit()
        write_rights_file(ctx.settings, project)
    ctx.publish_project()
    return info


def _thumbnail(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    duration = _media_info(ctx).get("duration") or 0
    at = min(duration * 0.1, 30.0) if duration else 0.0
    ctx.report(0.2, "Grabbing a poster frame")
    dest = ctx.settings.project_dir(ctx.project_id) / "thumbnail.jpg"
    make_thumbnail(ctx.settings, path, dest, at)
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.thumbnail = str(dest)
        s.commit()
    ctx.publish_project()
    return {"thumbnail": str(dest)}


def _proxy(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    info = _media_info(ctx)
    dest = ctx.settings.project_dir(ctx.project_id) / "proxy.mp4"
    make_proxy(ctx.settings, path, dest, info, lambda f: ctx.report(f, "Making a small preview copy"))
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.proxy_path = str(dest)
        s.commit()
    ctx.publish_project()
    ctx.report(1.0, f"Preview copy: {dest.stat().st_size / 1e6:.0f} MB")
    return {"proxy": str(dest), "bytes": dest.stat().st_size}


def _audio(ctx: StageContext) -> dict:
    path = _source_file(ctx)
    info = _media_info(ctx)
    if not info.get("has_audio"):
        ctx.report(1.0, "This video has no sound, so there is no audio to extract")
        return {"audio": None}
    dest = ctx.settings.project_dir(ctx.project_id) / "audio.wav"
    extract_audio(ctx.settings, path, dest, info, lambda f: ctx.report(f, "Extracting the audio track"))
    with ctx.session() as s:
        project = s.get(Project, ctx.project_id)
        project.audio_path = str(dest)
        s.commit()
    ctx.publish_project()
    ctx.report(1.0, f"Audio: {dest.stat().st_size / 1e6:.0f} MB")
    return {"audio": str(dest), "bytes": dest.stat().st_size}


def _ingest_finished(s: Session, job: Job) -> None:
    project = s.get(Project, job.project_id) if job.project_id else None
    if project is None:
        return
    if job.status == "done":
        project.status = "ready"
        project.status_detail = None
    elif job.status == "failed":
        project.status = "failed"
        project.status_detail = job.error
    elif job.status == "cancelled":
        project.status = "failed"
        project.status_detail = "Cancelled. Use Resume to continue from where it stopped."


def _ingest_retried(s: Session, job: Job) -> None:
    project = s.get(Project, job.project_id) if job.project_id else None
    if project is not None:
        project.status = "processing"
        project.status_detail = None


_PREPARE = [
    StageDef("probe", "Read video information", _probe),
    StageDef("thumbnail", "Create poster frame", _thumbnail),
    StageDef("proxy", "Make preview copy", _proxy),
    StageDef("audio", "Extract audio", _audio),
]


# ---- selftest ---------------------------------------------------------------

_TEST_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: D,Arial,90,&H00FFFFFF,&H00000000,&H80000000,1,1,6,2,2,40,40,420,1

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,D,SELF {\\c&H00FFFF&}TEST{\\c&HFFFFFF&} OK
"""


def _check_tools(ctx: StageContext) -> dict:
    ffmpeg = tool_version(ctx.settings.ffmpeg)
    ffprobe = tool_version(ctx.settings.ffprobe)
    if not ffmpeg or not ffprobe:
        raise MediaError("FFmpeg or ffprobe is missing from tools\\ffmpeg")
    ctx.report(1.0, f"FFmpeg {ffmpeg}")
    return {"ffmpeg": ffmpeg, "ffprobe": ffprobe}


def _check_render(ctx: StageContext) -> dict:
    """Render one second of 1080x1920 H.264/AAC with a burned-in caption."""
    ctx.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="selftest_", dir=ctx.settings.tmp_dir))
    try:
        (work / "cap.ass").write_text(_TEST_ASS, encoding="utf-8")
        ctx.report(0.2, "Rendering a test Short")
        # cwd is the work folder so the caption file needs no path escaping
        run(
            [ctx.settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=1",
             "-f", "lavfi", "-i", "sine=f=440:d=1",
             "-vf", "crop=608:1080,scale=1080:1920,ass=cap.ass",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", "out.mp4"],
            timeout=180, cwd=work,
        )
        ctx.report(0.8, "Checking the result")
        info = probe_media(ctx.settings, work / "out.mp4")
        if (info["width"], info["height"]) != (1080, 1920):
            raise MediaError(f"Expected 1080x1920, got {info['width']}x{info['height']}")
        if info["video_codec"] != "h264" or info["audio_codec"] != "aac":
            raise MediaError(f"Unexpected codecs: {info['video_codec']}/{info['audio_codec']}")
        ctx.report(1.0, "1080x1920 H.264/AAC with captions")
        return {"width": info["width"], "height": info["height"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _check_storage(ctx: StageContext) -> dict:
    ctx.settings.ensure_dirs()
    probe = ctx.settings.tmp_dir / "write_test.txt"
    probe.write_text("ok", encoding="utf-8")
    if probe.read_text(encoding="utf-8") != "ok":
        raise OSError("Data folder is not readable after writing")
    probe.unlink()
    free_gb = shutil.disk_usage(ctx.settings.data_dir).free / 1024**3
    if free_gb < 2:
        raise OSError(f"Only {free_gb:.1f} GB free on the data drive; at least 2 GB is needed")
    ctx.report(1.0, f"{free_gb:.1f} GB free")
    return {"free_gb": round(free_gb, 1)}


def _wait_stage(label: str):
    def fn(ctx: StageContext) -> dict:
        seconds = float(ctx.params.get("stage_seconds", 6))
        steps = max(1, int(seconds / 0.25))
        for i in range(steps):
            ctx.sleep(seconds / steps)
            ctx.report((i + 1) / steps, f"{label}: {int((i + 1) / steps * 100)}%")
        return {"seconds": seconds}

    return fn


PIPELINES: dict[str, Pipeline] = {
    "ingest_local": Pipeline(
        stages=list(_PREPARE), on_finish=_ingest_finished, on_retry=_ingest_retried
    ),
    "ingest_url": Pipeline(
        stages=[
            StageDef("metadata", "Look up the video", _metadata, max_attempts=3),
            StageDef("download", "Download the video", _download, max_attempts=3),
            *_PREPARE,
        ],
        on_finish=_ingest_finished,
        on_retry=_ingest_retried,
    ),
    "selftest": Pipeline(
        stages=[
            StageDef("tools", "Check FFmpeg", _check_tools, max_attempts=1),
            StageDef("render", "Render a captioned test Short", _check_render, max_attempts=1),
            StageDef("storage", "Check storage", _check_storage, max_attempts=1),
            StageDef("wait_a", "Timed stage A (no real work)", _wait_stage("Stage A"), max_attempts=1),
            StageDef("wait_b", "Timed stage B (no real work)", _wait_stage("Stage B"), max_attempts=1),
        ],
    ),
}
