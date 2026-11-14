"""Speech to text, with word timings.

The audio is transcribed in chunks of about ten minutes. Each chunk is saved as
soon as it finishes, so stopping the app in the middle of a three-hour video
loses at most one chunk. Chunk edges are placed at the quietest moment nearby
so a word is not cut in half.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import numpy as np

from ..config import Settings

log = logging.getLogger("clipforge.transcript")

SAMPLE_RATE = 16000
EDGE_SEARCH = 30.0  # seconds either side of the ideal chunk edge to look for quiet

# Speech-detection settings by Settings.speech_detection ("off" skips the filter).
VAD_PRESETS: dict[str, dict[str, Any] | None] = {
    "normal": None,  # the library's defaults
    "sensitive": {"threshold": 0.10, "min_silence_duration_ms": 1500, "speech_pad_ms": 800},
}


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


class Transcriber(Protocol):
    name: str

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[list[Segment], str | None]:
        """Return segments timed from the start of `audio`, and the language heard.

        `on_progress` gets the share of this audio done so far; it may raise to stop.
        """


def plan_chunks(
    duration: float, loudness: list[float], hop: float, target: float
) -> list[tuple[float, float]]:
    """Split [0, duration] into chunks near `target` seconds, cutting where it is quietest."""
    if duration <= 0:
        return []
    if duration <= target * 1.25:
        return [(0.0, round(duration, 2))]
    edges = [0.0]
    ideal = target
    # Look for a pause close to the ideal edge only: a wide search could skip a
    # whole chunk's worth of audio to reach a slightly quieter pause.
    search = min(EDGE_SEARCH, target * 0.15)
    while ideal < duration - target * 0.25:
        lo = max(edges[-1] + target * 0.5, ideal - search)
        hi = min(duration, ideal + search)
        a, b = int(lo / hop), int(hi / hop)
        window = loudness[a:b] if loudness else []
        cut = (a + int(np.argmin(window)) + 0.5) * hop if len(window) else ideal
        edges.append(round(float(cut), 2))
        ideal = cut + target
    edges.append(round(duration, 2))
    return list(zip(edges[:-1], edges[1:]))


class WhisperTranscriber:
    """faster-whisper on the CPU. The model is downloaded once into the data folder."""

    def __init__(self, settings: Settings) -> None:
        from faster_whisper import WhisperModel  # heavy import: only when transcribing

        self.name = f"whisper-{settings.whisper_model}"
        self._detection = settings.speech_detection if settings.speech_detection in ("normal", "off") else "sensitive"
        settings.models_dir.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
            download_root=str(settings.models_dir),
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[list[Segment], str | None]:
        length = max(len(audio) / SAMPLE_RATE, 0.01)
        raw, info = self._model.transcribe(
            audio,
            language=language,
            # Skipping non-speech is faster and avoids invented text over quiet
            # parts, but too strict a filter drops real speech under music.
            vad_filter=self._detection != "off",
            vad_parameters=VAD_PRESETS.get(self._detection),
            word_timestamps=True,
            beam_size=5,
            condition_on_previous_text=False,  # stops one mistake repeating down the file
        )
        segments: list[Segment] = []
        for seg in raw:
            if on_progress:
                on_progress(min(1.0, seg.end / length))
            text = seg.text.strip()
            if not text:
                continue
            # Speech the model itself doubts, over what it thinks is silence: drop, do not guess.
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            words = [
                Word(round(w.start, 2), round(w.end, 2), w.word.strip(), round(w.probability, 3))
                for w in (seg.words or [])
                if w.word.strip()
            ]
            # A segment's own start can sit at the end of the previous one, seconds
            # before anyone speaks. The first and last word are where speech really is.
            start = words[0].start if words else round(seg.start, 2)
            end = words[-1].end if words else round(seg.end, 2)
            segments.append(Segment(start, max(end, start), text, words))
        return segments, getattr(info, "language", None)


def make_transcriber(settings: Settings) -> Transcriber:
    return WhisperTranscriber(settings)


def model_is_downloaded(settings: Settings) -> bool:
    folder = settings.models_dir / f"models--Systran--faster-whisper-{settings.whisper_model}"
    return folder.is_dir() and any(folder.rglob("model.bin"))


def read_audio(path: str, start: float, end: float) -> np.ndarray:
    import soundfile as sf

    with sf.SoundFile(path) as f:
        sr = f.samplerate
        f.seek(int(start * sr))
        data = f.read(int((end - start) * sr), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"Expected {SAMPLE_RATE} Hz audio, got {sr} Hz")
    return data


MAX_WORD_SECONDS = 1.5  # nobody holds one word this long; the timing was stretched
STRETCHED_WORD_KEEPS = 0.8  # ...so keep this much before the word's end


def tighten(rows: list[dict[str, Any]], silences: list[list[float]]) -> list[dict[str, Any]]:
    """Correct line starts that the speech model stretched back across a pause.

    After a long silence the first word of the next line is sometimes timed from
    the end of the previous line, seconds before anyone speaks. Two independent
    checks pull it back: a single word cannot last that long, and a line cannot
    start inside a stretch the sound analysis measured as silent.
    """
    for row in rows:
        for w in row["words"]:
            if w["e"] - w["s"] > MAX_WORD_SECONDS:
                w["s"] = round(w["e"] - STRETCHED_WORD_KEEPS, 2)
        start = row["words"][0]["s"] if row["words"] else row["start"]
        for quiet_from, quiet_to in silences:
            covers_start = quiet_from <= start + 0.5 and quiet_to >= start + 1.0
            if covers_start and quiet_to < row["end"]:
                start = round(quiet_to, 2)
                if row["words"] and row["words"][0]["s"] < start < row["words"][0]["e"]:
                    row["words"][0]["s"] = start
                break
        row["start"] = min(start, row["end"])
    return rows


def offset_segments(segments: list[Segment], offset: float, limit: float) -> list[dict[str, Any]]:
    """Move chunk-relative times onto the whole video's clock, as plain dicts."""
    out = []
    for seg in segments:
        out.append({
            "start": round(min(seg.start + offset, limit), 2),
            "end": round(min(seg.end + offset, limit), 2),
            "text": seg.text,
            "words": [
                {"s": round(w.start + offset, 2), "e": round(w.end + offset, 2), "w": w.text, "p": w.prob}
                for w in seg.words
            ],
        })
    return out
