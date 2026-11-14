"""Loudness, silences and sudden rises, read from the 16 kHz mono WAV.

The file is read a minute at a time, so a three-hour recording never has to
fit in memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

FINE_HOP = 0.1  # seconds: resolution for finding silences
HOP = 0.5  # seconds: resolution of the stored curves
FLOOR_DB = -80.0

MIN_SILENCE = 0.7  # seconds
SILENCE_DROP_DB = 20.0  # this far under the recording's active level counts as silence
SURGE_OVER_ACTIVE_DB = 3.0  # a surge must also be louder than the recording normally gets
SURGE_WINDOW = 10.0  # seconds of history a surge is compared with
SURGE_DB = 9.0  # how far above that history counts as a surge
SURGE_MERGE = 2.0  # surges closer than this are one event


def _db(rms: np.ndarray) -> np.ndarray:
    return np.maximum(20.0 * np.log10(rms + 1e-9), FLOOR_DB)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [start, end) where mask is True."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2])]


def analyse_audio(wav: Path, on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    fine_rms: list[np.ndarray] = []
    centroids: list[np.ndarray] = []

    with sf.SoundFile(str(wav)) as f:
        sr = f.samplerate
        total = max(1, f.frames)
        fine = int(sr * FINE_HOP)
        coarse = int(sr * HOP)
        block = coarse * 120  # one minute
        window = np.hanning(coarse).astype(np.float32)
        freqs = np.fft.rfftfreq(coarse, 1.0 / sr).astype(np.float32)
        done = 0
        for data in f.blocks(blocksize=block, dtype="float32", always_2d=False):
            if data.ndim > 1:
                data = data.mean(axis=1)
            done += len(data)
            pad = (-len(data)) % coarse
            if pad:
                data = np.concatenate([data, np.zeros(pad, dtype=np.float32)])
            fine_rms.append(np.sqrt(np.mean(data.reshape(-1, fine) ** 2, axis=1)))
            spectrum = np.abs(np.fft.rfft(data.reshape(-1, coarse) * window, axis=1))
            centroids.append((spectrum * freqs).sum(axis=1) / (spectrum.sum(axis=1) + 1e-9))
            if on_progress:
                on_progress(min(0.99, done / total))
        duration = total / sr

    if not fine_rms:
        return {"hop": HOP, "duration": 0.0, "loudness": [], "brightness": [], "silences": [], "events": []}

    fine_db = _db(np.concatenate(fine_rms))
    per = round(HOP / FINE_HOP)
    usable = len(fine_db) - len(fine_db) % per
    # loudest tenth of a second in each half second: keeps short shouts visible
    loud = fine_db[:usable].reshape(-1, per).max(axis=1)
    bright = np.concatenate(centroids)[: len(loud)]

    floor = float(np.percentile(fine_db, 10))
    # The level this recording sits at when something is going on (talking,
    # gameplay). Everything else is judged against it, never against a fixed
    # number: a noisy room never reaches -60 dB and a quiet one rarely passes -20.
    active = float(np.percentile(fine_db, 90))
    silence_below = active - SILENCE_DROP_DB
    # How loud this recording gets while something is happening, measured on the
    # same half-second peaks a surge is tested against and ignoring the quiet
    # parts. Otherwise a video that is mostly pauses rates every sentence as loud.
    busy = loud[loud > silence_below]
    busy_level = float(np.percentile(busy, 90)) if len(busy) else active
    surge_above = busy_level + SURGE_OVER_ACTIVE_DB

    silences = [
        [round(a * FINE_HOP, 2), round(min(b * FINE_HOP, duration), 2)]
        for a, b in _runs(fine_db < silence_below)
        if (b - a) * FINE_HOP >= MIN_SILENCE
    ]

    # A surge is a half-second much louder than the ten seconds before it.
    n_hist = int(SURGE_WINDOW / HOP)
    events: list[dict[str, Any]] = []
    if len(loud) > n_hist:
        history = np.lib.stride_tricks.sliding_window_view(loud, n_hist)[:-1]
        # After a long pause the history is near-silence; measure the rise from
        # the quiet threshold instead so "rise" stays a meaningful number.
        baseline = np.maximum(np.median(history, axis=1), silence_below)
        rise = loud[n_hist:] - baseline
        # Much louder than the last ten seconds AND louder than this recording
        # normally gets: someone starting to talk after a pause is not a surge.
        strong = (rise >= SURGE_DB) & (loud[n_hist:] >= surge_above)
        quiet_ends = np.array([s[1] for s in silences]) if silences else np.array([])
        for a, b in _runs(strong):
            peak = a + int(np.argmax(rise[a:b]))
            t = (peak + n_hist) * HOP
            start = (a + n_hist) * HOP
            if events and start - events[-1]["t"] < SURGE_MERGE:
                if rise[peak] > events[-1]["rise_db"]:
                    events[-1].update(t=round(t, 2), rise_db=round(float(rise[peak]), 1))
                continue
            after_silence = bool(len(quiet_ends) and np.any((quiet_ends <= start + 0.3) & (quiet_ends >= start - 1.5)))
            events.append({
                "t": round(t, 2),
                "kind": "surge_after_silence" if after_silence else "surge",
                "rise_db": round(float(rise[peak]), 1),
            })

    return {
        "hop": HOP,
        "duration": round(duration, 3),
        "loudness": [round(float(v), 1) for v in loud],
        "brightness": [int(v) for v in bright],
        "silences": silences,
        "events": events,
        "stats": {
            "floor_db": round(floor, 1),
            "active_db": round(active, 1),
            "busy_level_db": round(busy_level, 1),
            "silence_below_db": round(silence_below, 1),
            "surge_above_db": round(surge_above, 1),
            "silence_seconds": round(sum(e - s for s, e in silences), 1),
        },
    }
