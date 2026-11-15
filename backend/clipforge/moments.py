"""Finding the moments worth clipping, and scoring them.

Three steps, each narrower than the last:

1. The model reads the whole transcript in overlapping stretches, with pauses
   and sudden loud moments marked in, and proposes moments with seven
   sub-scores and a reason. This is where setups and punchlines are understood.
2. Sudden loud moments the transcript pass did not pick (a jump scare has no
   dialogue) are added as extra candidates.
3. The best candidates are checked against frames from the video, so what is
   on screen can raise, lower or reject them.

The viral score is computed here, in code, from the sub-scores and the
weights in settings, so it can be explained and re-tuned without asking the
model again. Exact cut points are Phase 5's job; the ranges here are rough.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .ai import AIError, Reasoner
from .config import Settings
from .media import MediaError
from .schemas import CATEGORIES, ProjectOptions

log = logging.getLogger("clipforge.moments")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

SUBSCORES = ["entertainment", "emotion", "surprise", "retention", "context", "replay", "standalone"]
DEFAULT_WEIGHTS = {
    "entertainment": 0.22, "emotion": 0.15, "surprise": 0.18, "retention": 0.15,
    "context": 0.10, "replay": 0.10, "standalone": 0.10,
}
SUBSCORE_MEANING = {
    "entertainment": "how fun or gripping it is to watch",
    "emotion": "how strong the feeling is (laughter, fear, anger, joy)",
    "surprise": "how unexpected the turn is",
    "retention": "how likely a viewer is to watch to the end",
    "context": "whether the setup and the payoff are both there",
    "replay": "how likely a viewer is to watch it again or share it",
    "standalone": "whether it makes sense with no knowledge of the rest of the video",
}

CHUNK_SECONDS = 20 * 60
CHUNK_OVERLAP = 60
PER_CHUNK = 6  # moments asked for per stretch of transcript
LONG_PAUSE = 3.0
VISUAL_BATCH = 4
SHEET_FRAMES = 6


@dataclass
class Line:
    n: int  # 1-based number shown to the model
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    category: str
    subscores: dict[str, int]
    reason: str
    excerpt: str = ""
    source: str = "ai_text"  # ai_text | signals
    visual: str | None = None
    visual_checked: bool = False
    score: int = 0
    selected: bool = False
    rank: int = 0
    # the span as picked, before the rough length fit: what Smart Duration cuts from
    core_start: float | None = None
    core_end: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def length(self) -> float:
        return self.end - self.start


# ---- scoring -----------------------------------------------------------------


def clean_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    """Weights for every sub-score, non-negative, summing to 1."""
    out = {}
    for key in SUBSCORES:
        try:
            out[key] = max(0.0, float((raw or {}).get(key, DEFAULT_WEIGHTS[key])))
        except (TypeError, ValueError):
            out[key] = DEFAULT_WEIGHTS[key]
    total = sum(out.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: round(v / total, 4) for k, v in out.items()}


def viral_score(subscores: dict[str, Any], weights: dict[str, float]) -> int:
    """0-100 from seven 0-10 sub-scores."""
    total = sum(weights[k] * _clamp_score(subscores.get(k)) for k in SUBSCORES)
    return int(round(total * 10))


def _clamp_score(value: Any) -> int:
    try:
        return max(0, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def clean_subscores(raw: Any) -> dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: _clamp_score(raw.get(k)) for k in SUBSCORES}


# ---- step 1: the transcript pass ----------------------------------------------


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def chunk_lines(lines: list[Line]) -> list[list[Line]]:
    """Overlapping stretches of about twenty minutes."""
    if not lines:
        return []
    chunks: list[list[Line]] = []
    i = 0
    while i < len(lines):
        limit = lines[i].start + CHUNK_SECONDS
        j = i
        while j < len(lines) and lines[j].start < limit:
            j += 1
        chunks.append(lines[i:j])
        if j >= len(lines):
            break
        back = j
        while back > i + 1 and lines[back - 1].start > lines[j - 1].start - CHUNK_OVERLAP:
            back -= 1
        i = back
    return chunks


def annotate(chunk: list[Line], events: list[dict[str, Any]]) -> str:
    """The transcript as the model sees it: numbered, timed, with pauses and loud moments marked."""
    out: list[str] = []
    previous_end: float | None = None
    for line in chunk:
        if previous_end is not None and line.start - previous_end >= LONG_PAUSE:
            out.append(f"      ({line.start - previous_end:.0f} second pause)")
        marks = [
            "sudden loud moment after silence" if e["kind"] == "surge_after_silence" else "sudden loud moment"
            for e in events
            if line.start - 1.0 <= e["t"] <= line.end + 1.0
        ]
        suffix = f"   <{'; '.join(sorted(set(marks)))}>" if marks else ""
        out.append(f"L{line.n} [{_clock(line.start)}] {line.text}{suffix}")
        previous_end = line.end
    return "\n".join(out)


_SCORES_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "integer", "minimum": 0, "maximum": 10} for k in SUBSCORES},
    "required": SUBSCORES,
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "peak_line": {"type": "integer"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "scores": _SCORES_SCHEMA,
                    "reason": {"type": "string"},
                },
                "required": ["start_line", "end_line", "peak_line", "category", "scores", "reason"],
            },
        }
    },
    "required": ["moments"],
}


def _rubric() -> str:
    return "\n".join(f"- {k}: {SUBSCORE_MEANING[k]}" for k in SUBSCORES)


def text_task(listing: str, categories: list[str], min_len: int, max_len: int, how_many: int) -> str:
    return f"""You are an experienced editor of short vertical videos. Below is part of the transcript of a long video.
Find the moments in it that would make the best standalone short clips.

Categories to look for: {", ".join(categories)}.

How to read the transcript: each line is "L<number> [time] words". "(N second pause)" marks a gap in speech.
"<sudden loud moment>" marks a line where the sound suddenly got much louder than usual. Loudness is a hint
only: many loud lines are dull and many great moments are quiet.

Rules:
- A moment must make sense to someone who has not seen the rest of the video. Start it at the line that sets
  it up and end it after the punchline or reaction has finished.
- Aim for {min_len} to {max_len} seconds from the first line's time to the last line's.
- Use only the lines below. Never describe something the lines do not show. If you are not sure what
  happened, say so in the reason instead of inventing it.
- Do not pick the same event twice.
- Return at most {how_many} moments, best first. If nothing here is worth a clip, return an empty list.
  Ordinary conversation is not a moment.

For each moment give:
- start_line, end_line: line numbers from below (the L numbers).
- peak_line: the line where the key thing happens.
- category: one of the categories above.
- scores: each from 0 (not at all) to 10 (exceptional). Be strict: 5 is average, and most moments are average.
{_rubric()}
- reason: one or two plain sentences on why this moment works, quoting the key words.

Transcript:
{listing}
"""


def _excerpt(lines: list[Line], limit: int = 600) -> str:
    text = " ".join(line.text for line in lines)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fit_length(start: float, end: float, peak: float, min_len: float, max_len: float, total: float) -> tuple[float, float]:
    """Rough fit to the allowed length, keeping the key moment inside. Phase 5 does the careful cut."""
    start, end = max(0.0, start), min(total, end) if total else end
    length = end - start
    if length > max_len:
        # keep more before the key moment than after it: the setup is what gets lost otherwise
        new_start = max(start, min(peak - 0.6 * max_len, end - max_len))
        start, end = new_start, min(end, new_start + max_len)
    elif length < min_len:
        missing = min_len - length
        start = max(0.0, start - 0.6 * missing)
        end = start + min_len
        if total and end > total:
            end = total
            start = max(0.0, end - min_len)
    return round(start, 2), round(end, 2)


def parse_text_moments(
    answer: dict[str, Any], chunk: list[Line], categories: list[str],
    min_len: float, max_len: float, total: float,
) -> list[Candidate]:
    by_number = {line.n: line for line in chunk}
    found: list[Candidate] = []
    for item in answer.get("moments") or []:
        if not isinstance(item, dict):
            continue
        first, last = by_number.get(item.get("start_line")), by_number.get(item.get("end_line"))
        if first is None or last is None or last.n < first.n:
            continue  # the model referred to lines it was not given: do not guess
        category = item.get("category")
        if category not in categories:
            continue
        peak_line = by_number.get(item.get("peak_line"))
        peak = peak_line.start if peak_line and first.n <= peak_line.n <= last.n else first.start
        start, end = fit_length(first.start, last.end, peak, min_len, max_len, total)
        inside = [line for line in chunk if line.end > start and line.start < end]
        found.append(Candidate(
            start=start, end=end, peak=round(peak, 2), category=category,
            subscores=clean_subscores(item.get("scores")),
            reason=str(item.get("reason") or "").strip()[:600],
            excerpt=_excerpt(inside),
            core_start=round(first.start, 2), core_end=round(last.end, 2),
        ))
    return found


def length_bounds(options: dict[str, Any]) -> tuple[float, float]:
    """The shortest and longest moment to ask the AI for, from the project's Smart Duration settings."""
    if "target_duration" not in options and "min_duration" in options:
        # a project saved before Smart Duration existed
        return float(options.get("min_duration", 20)), float(options.get("max_duration", 60))
    st = ProjectOptions(**options).duration_settings()
    # with part splitting on, a moment may run as long as all its parts together
    longest = st.high * (st.max_parts if st.split else 1)
    # whole seconds, rounded down: the longest is a limit, so never round it up
    return float(max(8, int(0.5 * st.target))), float(int(longest + 1e-6))


# ---- step 2: loud moments the transcript pass missed ---------------------------


def signal_candidates(
    events: list[dict[str, Any]], covered: list[Candidate], lines: list[Line],
    min_len: float, max_len: float, total: float, limit: int = 6,
) -> list[Candidate]:
    target = (min_len + max_len) / 2
    out: list[Candidate] = []
    for event in sorted(events, key=lambda e: -e.get("rise_db", 0)):
        t = float(event["t"])
        if any(c.start - 3 <= t <= c.end + 3 for c in covered + out):
            continue
        start = max(0.0, t - 0.6 * target)
        end = min(total, start + target) if total else start + target
        inside = [line for line in lines if line.end > start and line.start < end]
        out.append(Candidate(
            start=round(start, 2), end=round(end, 2), peak=round(t, 2), category="unexpected",
            subscores={k: 0 for k in SUBSCORES},
            reason=f"The sound suddenly rose by {event.get('rise_db', 0):.0f} dB here.",
            excerpt=_excerpt(inside), source="signals",
            core_start=round(start, 2), core_end=round(end, 2),
        ))
        if len(out) >= limit:
            break
    return out


# ---- step 3: the look at the picture -------------------------------------------


def make_sheet(settings: Settings, video: Path, start: float, end: float, dest: Path) -> Path:
    """Six frames spread across the moment, in one image."""
    if not settings.ffmpeg:
        raise MediaError("ffmpeg is not installed (expected in tools\\ffmpeg)")
    length = max(end - start, 1.0)
    step = length / SHEET_FRAMES
    proc = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{start + step / 2:.2f}", "-t", f"{length:.2f}", "-i", str(video),
         "-vf", f"fps=1/{step:.3f},scale=320:-2,tile=3x2", "-frames:v", "1", "-q:v", "5", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError("Could not grab frames for a moment: " + (proc.stderr or "").strip()[-200:])
    return dest


VISUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "seen": {"type": "string"},
                    "picture_useful": {"type": "boolean"},
                    "keep": {"type": "boolean"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "scores": _SCORES_SCHEMA,
                },
                "required": ["id", "seen", "picture_useful", "keep", "category", "scores"],
            },
        }
    },
    "required": ["moments"],
}


def visual_task(batch: list[tuple[int, Candidate, str]], categories: list[str]) -> str:
    blocks = []
    for ident, c, image in batch:
        prior = (
            "no scores yet: this was picked only because the sound suddenly got loud"
            if c.source == "signals"
            else ", ".join(f"{k} {c.subscores[k]}" for k in SUBSCORES) + f" (category: {c.category})"
        )
        blocks.append(
            f"Moment {ident}: {_clock(c.start)} to {_clock(c.end)}, key moment at {_clock(c.peak)}.\n"
            f"Image file: {image} (six frames in time order: left to right, top row then bottom row).\n"
            f"What is said: {c.excerpt or '(no speech)'}\n"
            f"Scores from the words alone: {prior}\n"
            f"Why it was picked: {c.reason}"
        )
    return f"""You are an experienced editor of short vertical videos, checking candidate moments against the picture.
For each moment below, open its image file and look at the frames, then judge the moment using both the
words and what you can see.

Rules:
- Describe only what is visible in the frames. Do not invent events, people or reactions.
- "seen": one plain sentence on what the frames show.
- "picture_useful": true if the frames tell you something about the moment (a reaction, an event, what is
  going on). false if they add nothing: a static screen, a test pattern, a menu, a black or frozen frame.
- A picture that adds nothing is NOT a reason to mark a moment down. Many good moments are carried by the
  words alone. When "picture_useful" is false, repeat the scores and category you were given, unchanged.
- "keep": for a moment that has scores from the words, always true. For a moment picked only because the
  sound got loud, true only if the frames show something worth a short clip.
- "category": one of {", ".join(categories)}.
- "scores": each 0 to 10, strict (5 is average). Start from the scores given and change them only as far as
  the picture justifies, up or down:
{_rubric()}
- Return one entry per moment, using the same moment numbers.

{chr(10).join(chr(10) + b for b in blocks)}
"""


def apply_visual(answer: dict[str, Any], batch: list[tuple[int, Candidate, str]], categories: list[str]) -> None:
    by_id = {ident: c for ident, c, _ in batch}
    for item in answer.get("moments") or []:
        c = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if c is None:
            continue
        c.visual = str(item.get("seen") or "").strip()[:400] or None
        c.visual_checked = True
        useful = bool(item.get("picture_useful", True))
        c.extra["picture_useful"] = useful
        if c.source == "signals":
            # Nothing but a loud sound vouches for this one, so the picture has to.
            c.extra["keep"] = useful and bool(item.get("keep", False))
        elif not useful:
            # A moment picked from the words keeps its scores when the picture says nothing.
            continue
        c.subscores = clean_subscores(item.get("scores"))
        if item.get("category") in categories:
            c.category = item["category"]


# ---- putting it together -------------------------------------------------------


def drop_duplicates(candidates: list[Candidate]) -> list[Candidate]:
    """Best first; drop any that overlaps a better one by more than half of the shorter of the two."""
    kept: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: (-c.score, c.start)):
        for k in kept:
            overlap = min(c.end, k.end) - max(c.start, k.start)
            if overlap > 0.5 * min(c.length, k.length):
                break
        else:
            kept.append(c)
    return kept


def select(candidates: list[Candidate], options: dict[str, Any]) -> list[Candidate]:
    """Rank by score and mark the ones the project's settings ask for."""
    wanted = set(options.get("categories") or CATEGORIES)
    min_score = int(options.get("min_score", 0))
    limit = int(options.get("max_clips", 8))
    ranked = sorted(candidates, key=lambda c: (-c.score, c.start))
    taken = 0
    for i, c in enumerate(ranked, start=1):
        c.rank = i
        c.selected = c.category in wanted and c.score >= min_score and taken < limit
        taken += c.selected
    return ranked


def detect_moments(
    reasoner: Reasoner,
    settings: Settings,
    *,
    lines: list[Line],
    events: list[dict[str, Any]],
    duration: float,
    proxy: Path | None,
    options: dict[str, Any],
    weights: dict[str, float],
    work_dir: Path,
    report: Callable[[float, str], None],
    check: Callable[[], None] | None = None,
) -> tuple[list[Candidate], dict[str, Any]]:
    """`report(progress, message)` updates the progress bar; `check()` raises if the job was cancelled."""
    categories = [c for c in CATEGORIES if c in (options.get("categories") or CATEGORIES)]
    min_len, max_len = length_bounds(options)
    max_clips = int(options.get("max_clips", 8))
    notes: dict[str, Any] = {"text_calls": 0, "visual_calls": 0, "visual_failed": 0, "provider": reasoner.name}

    # 1. the transcript
    candidates: list[Candidate] = []
    chunks = chunk_lines(lines)
    for i, chunk in enumerate(chunks):
        report(0.6 * i / max(len(chunks), 1),
               f"Reading the transcript: {_clock(chunk[0].start)} to {_clock(chunk[-1].end)}")
        answer = reasoner.ask_json(
            text_task(annotate(chunk, events), categories, int(min_len), int(max_len), PER_CHUNK),
            TEXT_SCHEMA, check=check,
        )
        notes["text_calls"] += 1
        candidates += parse_text_moments(answer, chunk, categories, min_len, max_len, duration)
    for c in candidates:
        c.score = viral_score(c.subscores, weights)
    candidates = drop_duplicates(candidates)
    notes["from_words"] = len(candidates)

    # 2. loud moments nobody talked over
    extra = signal_candidates(events, candidates, lines, min_len, max_len, duration)
    notes["from_sound"] = len(extra)

    # 3. the picture, for the candidates that could make the cut
    to_check = candidates[: max(2 * max_clips, VISUAL_BATCH)] + extra
    if proxy and proxy.is_file() and to_check:
        work_dir.mkdir(parents=True, exist_ok=True)
        batches = [to_check[i:i + VISUAL_BATCH] for i in range(0, len(to_check), VISUAL_BATCH)]
        for b, group in enumerate(batches):
            report(0.6 + 0.4 * b / len(batches), f"Checking the picture: group {b + 1} of {len(batches)}")
            batch: list[tuple[int, Candidate, str]] = []
            sheets: list[Path] = []
            for k, c in enumerate(group, start=1):
                sheet = make_sheet(settings, proxy, c.start, c.end, work_dir / f"moment_{b + 1}_{k}.jpg")
                sheets.append(sheet)
                batch.append((k, c, sheet.name))
            try:
                answer = reasoner.ask_json(visual_task(batch, categories), VISUAL_SCHEMA, images=sheets, check=check)
                notes["visual_calls"] += 1
                apply_visual(answer, batch, categories)
            except AIError as exc:
                # The words-only scores still stand; say so rather than fail the whole run.
                notes["visual_failed"] += 1
                log.warning("Picture check failed for one group: %s", exc)
            finally:
                for sheet in sheets:
                    sheet.unlink(missing_ok=True)

    # A loud moment only counts if the picture check actually vouched for it.
    # Moments picked from the words are never dropped by the picture check.
    kept_extra = [c for c in extra if c.visual_checked and c.extra.get("keep")]
    notes["from_sound_kept"] = len(kept_extra)
    notes["picture_added_nothing"] = sum(1 for c in candidates if c.extra.get("picture_useful") is False)
    candidates = candidates + kept_extra
    for c in candidates:
        c.score = viral_score(c.subscores, weights)
    ranked = select(drop_duplicates(candidates), options)
    notes["provider"] = reasoner.name  # the model that actually answered
    notes.update(found=len(ranked), selected=sum(c.selected for c in ranked),
                 visually_checked=sum(c.visual_checked for c in ranked))
    return ranked, notes
