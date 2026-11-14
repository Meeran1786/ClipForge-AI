"""FFmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .config import Settings

# Never flash a console window when the app runs without a terminal.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".wmv"}


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], *, timeout: float = 120, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).stem} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise MediaError(f"{Path(cmd[0]).stem} failed: {' | '.join(tail) or 'no output'}")
    return proc


def tool_version(exe: str | None) -> str | None:
    if not exe:
        return None
    try:
        first = run([exe, "-version"], timeout=15).stdout.splitlines()[0]
    except (MediaError, OSError, IndexError):
        return None
    parts = first.split()
    return parts[2] if len(parts) > 2 else first


def _ratio(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return round(float(num) / float(den), 3) if float(den) else None
        return round(float(value), 3)
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe_media(settings: Settings, path: Path) -> dict[str, Any]:
    """Read container and stream facts from a media file."""
    if not settings.ffprobe:
        raise MediaError("ffprobe is not installed (expected in tools\\ffmpeg)")
    if not path.is_file():
        raise MediaError(f"File not found: {path}")
    out = run(
        [settings.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    ).stdout
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError("This file has no video stream")

    duration = _float(fmt.get("duration")) or _float(video.get("duration"))
    return {
        "duration": round(duration, 3) if duration else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "has_audio": audio is not None,
        "format": fmt.get("format_long_name") or fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else path.stat().st_size,
    }


def run_with_progress(
    cmd: list[str],
    *,
    duration: float | None,
    on_progress: Callable[[float], None],
) -> None:
    """Run FFmpeg and report how far through the input it is.

    `cmd` must contain `-progress pipe:1 -nostats`. `on_progress` may raise (job
    cancelled, app closing); FFmpeg is then stopped and the exception passes on.
    """
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=err, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    value = line.split("=", 1)[1].strip()
                    if value.lstrip("-").isdigit() and duration:
                        on_progress(max(0.0, min(1.0, int(value) / 1e6 / duration)))
                    else:
                        on_progress(0.0)
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            raise MediaError(f"ffmpeg failed: {' | '.join(tail) or 'no output'}")


def _finish_part(part: Path, dest: Path) -> Path:
    if not part.is_file() or part.stat().st_size == 0:
        raise MediaError(f"FFmpeg produced no output for {dest.name}")
    os.replace(part, dest)  # only a complete file ever has the final name
    return dest


def make_proxy(
    settings: Settings, src: Path, dest: Path, info: dict[str, Any], on_progress: Callable[[float], None]
) -> Path:
    """Small 360p H.264 copy: quick to scrub in the dashboard and cheap to send to AI."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fps = min(30.0, float(info.get("fps") or 30.0))
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min(360,ih)',fps={fps:g}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-g", str(max(1, round(fps * 2))), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(part),
    ]
    run_with_progress(cmd, duration=info.get("duration"), on_progress=on_progress)
    return _finish_part(part, dest)


def extract_audio(
    settings: Settings, src: Path, dest: Path, info: dict[str, Any], on_progress: Callable[[float], None]
) -> Path:
    """16 kHz mono WAV: what speech recognition and audio analysis expect."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-progress", "pipe:1", "-nostats", str(part),
    ]
    run_with_progress(cmd, duration=info.get("duration"), on_progress=on_progress)
    return _finish_part(part, dest)


def make_thumbnail(settings: Settings, src: Path, dest: Path, at_seconds: float) -> Path:
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(src),
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(dest)],
        timeout=120,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError("Could not read a frame from the video")
    return dest
