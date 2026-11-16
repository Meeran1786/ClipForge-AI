"""Measured sound and picture quality of a candidate moment (Phase 11.1).

These are measurements, not judgements: each turns a few plain signals into a
0-10 score with the reasons that took points off, so the owner can see why.

* Sound: how loud the speech is, how much of the stretch is silent, whether
  the sound clips, and how far the voice stands out from the background.
* Picture: sharpness, brightness, black frames, and how much of the stretch
  stands still.

Neither says anything about whether the moment is entertaining; that is the
AI's part of the score.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .media import MediaError

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

FRAME = 0.05  # seconds per loudness frame
SILENT_DB = -50.0
QUIET_SPEECH_DB = -38.0  # the loud end of the stretch below this: hard to hear
CLIP_LEVEL = 0.985
SAMPLE_FRAMES = 5
STILL_MOTION = 0.002  # frame-to-frame change below this counts as standing still


def _db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2, axis=-1)) + 1e-9)


def audio_quality(samples: np.ndarray, rate: int) -> tuple[int, dict[str, Any]]:
    """0-10 for the sound of a stretch (mono samples, -1..1), with what lowered it."""
    n = int(FRAME * rate)
    if samples.size < n * 4:
        return 0, {"notes": ["too short to measure"]}
    frames = samples[: samples.size // n * n].reshape(-1, n)
    levels = _db(frames)
    loud, floor = float(np.percentile(levels, 75)), float(np.percentile(levels, 10))
    silent = float(np.mean(levels < SILENT_DB))
    clipped = float(np.mean(np.abs(samples) >= CLIP_LEVEL))
    score, notes = 10.0, []
    if clipped > 0.001:
        score -= 3
        notes.append(f"the sound clips ({clipped:.1%} of samples at full level)")
    if silent > 0.4:
        score -= 3
        notes.append(f"silent {silent:.0%} of the time")
    elif silent > 0.2:
        score -= 1.5
        notes.append(f"silent {silent:.0%} of the time")
    if loud < QUIET_SPEECH_DB:
        score -= 2
        notes.append(f"quiet (loud parts at {loud:.0f} dBFS)")
    contrast = loud - floor
    if silent <= 0.2 and contrast < 8:
        score -= 2
        notes.append(f"the voice barely stands out from the background ({contrast:.0f} dB)")
    elif silent <= 0.2 and contrast < 12:
        score -= 1
        notes.append(f"busy background ({contrast:.0f} dB between voice and background)")
    detail = {"loud_db": round(loud, 1), "floor_db": round(floor, 1), "silent_share": round(silent, 2),
              "clipped_share": round(clipped, 4), "notes": notes}
    return int(round(max(0.0, score))), detail


def picture_quality(frames: list[np.ndarray], still_share: float | None) -> tuple[int, dict[str, Any]]:
    """0-10 for the picture of a stretch from a few grey frames, with what lowered it."""
    import cv2

    if not frames:
        return 0, {"notes": ["no frames could be read"]}
    brightness = [float(f.mean()) for f in frames]
    sharpness = [float(cv2.Laplacian(f, cv2.CV_64F).var()) for f in frames]
    black = float(np.mean([b < 16 for b in brightness]))
    mean_b, sharp = float(np.mean(brightness)), float(np.median(sharpness))
    score, notes = 10.0, []
    if black > 0:
        score -= 4 * black
        notes.append(f"black picture in {black:.0%} of the sampled frames")
    if black < 1 and (mean_b < 35 or mean_b > 225):
        score -= 3
        notes.append("very dark" if mean_b < 35 else "very bright")
    if sharp < 30:
        score -= 3
        notes.append("blurry")
    elif sharp < 80:
        score -= 1.5
        notes.append("a little soft")
    if still_share is not None:
        if still_share > 0.6:
            score -= 3
            notes.append(f"the picture stands still {still_share:.0%} of the time")
        elif still_share > 0.3:
            score -= 1
            notes.append(f"the picture stands still {still_share:.0%} of the time")
    detail = {"brightness": round(mean_b), "sharpness": round(sharp), "black_share": round(black, 2),
              "still_share": None if still_share is None else round(still_share, 2), "notes": notes}
    return int(round(max(0.0, score))), detail


def read_grey_frames(settings: Settings, video: Path, start: float, end: float, count: int = SAMPLE_FRAMES,
                     width: int = 320) -> list[np.ndarray]:
    """A few grey frames spread across a stretch of the (proxy) video."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    out: list[np.ndarray] = []
    length = max(end - start, 0.5)
    for k in range(count):
        t = start + length * (k + 0.5) / count
        proc = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:-2,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60, creationflags=_NO_WINDOW,
        )
        raw = proc.stdout
        if proc.returncode != 0 or not raw:
            continue
        h = len(raw) // width
        if h > 0:
            out.append(np.frombuffer(raw[: width * h], np.uint8).reshape(h, width))
    return out


def still_share(motion: list[float], hop: float, start: float, end: float) -> float | None:
    if not motion or hop <= 0:
        return None
    window = motion[int(start / hop): max(int(end / hop), int(start / hop) + 1)]
    return float(np.mean([m < STILL_MOTION for m in window])) if window else None


def measure(settings: Settings, audio: Path | None, proxy: Path | None, motion: list[float], hop: float,
            start: float, end: float) -> dict[str, Any]:
    """Measured audio and visual quality for one stretch. A part that cannot be measured is left out."""
    out: dict[str, Any] = {}
    if audio is not None and audio.is_file():
        from .analysis.transcript import read_audio

        samples = read_audio(str(audio), start, end)
        out["audio_quality"], out["audio_detail"] = audio_quality(np.asarray(samples, np.float32), 16000)
    if proxy is not None and proxy.is_file():
        frames = read_grey_frames(settings, proxy, start, end)
        out["visual_quality"], out["visual_detail"] = picture_quality(frames, still_share(motion, hop, start, end))
    return out
