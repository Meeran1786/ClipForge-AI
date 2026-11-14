"""Scene cuts and on-screen motion, read from the 360p preview copy.

FFmpeg hands over four tiny frames a second, so even a three-hour video is one
quick pass.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import Settings
from ..media import MediaError

FPS = 4
W, H = 64, 36
HOP = 0.5
CUT_MIN = 0.08  # share of full-scale change that can be a cut at all
CUT_RATIO = 4.0  # ...and how many times the recent motion it must be
CUT_GAP = 1.0  # seconds between cuts

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def analyse_video(
    settings: Settings,
    video: Path,
    duration: float | None,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    frame_bytes = W * H * 3
    cmd = [
        settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-an", "-vf", f"fps={FPS},scale={W}:{H}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    diffs: list[float] = []
    expected = max(1.0, (duration or 0) * FPS)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_NO_WINDOW)
        try:
            assert proc.stdout is not None
            previous: np.ndarray | None = None
            while True:
                raw = proc.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
                diffs.append(0.0 if previous is None else float(np.abs(frame - previous).mean() / 255.0))
                previous = frame
                if on_progress and len(diffs) % (FPS * 30) == 0:
                    on_progress(min(0.99, len(diffs) / expected))
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-2:]
            raise MediaError(f"ffmpeg failed: {' | '.join(tail) or 'no output'}")

    d = np.asarray(diffs, dtype=np.float32)
    cuts: list[float] = []
    look_back = FPS * 2
    for i in range(1, len(d)):
        recent = d[max(1, i - look_back):i]
        usual = float(np.median(recent)) if len(recent) else 0.0
        if d[i] >= CUT_MIN and d[i] >= CUT_RATIO * usual:
            t = i / FPS
            if not cuts or t - cuts[-1] >= CUT_GAP:
                cuts.append(round(t, 2))

    per = round(HOP * FPS)
    usable = len(d) - len(d) % per
    motion = d[:usable].reshape(-1, per).max(axis=1) if usable else np.array([], dtype=np.float32)
    return {
        "hop": HOP,
        "frames": len(d),
        "motion": [round(float(v), 4) for v in motion],
        "cuts": cuts,
    }
