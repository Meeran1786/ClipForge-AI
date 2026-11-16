"""Word-synced captions for a clip.

Captions are built from the word timings the speech model already produced for
the whole video (Phase 3), moved onto the clip's own clock. Nothing is
re-transcribed and nothing is invented: every caption word is a word the model
heard, in order, and words it was unsure of are listed for review rather than
guessed at.

The output is an ASS subtitle file sized for a 1080x1920 Short, burned in by
FFmpeg (libass). The word being spoken is highlighted, karaoke style, as in
the reference Short the owner chose.
"""

from __future__ import annotations

import math
import struct
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import REPO_ROOT

FONTS_DIR = REPO_ROOT / "tools" / "fonts"

FRAME_W, FRAME_H = 1080, 1920
HOLD = 0.3  # a line stays up this long after its last word, unless the next line starts sooner
LINE_BREAK_PAUSE = 0.6  # a pause this long starts a new line
LOW_CONFIDENCE = 0.5  # words the speech model was less sure of than this are flagged
LABEL_ZONE = 0.18  # top share of the frame kept clear for the Part 1 / Part 2 label (Phase 8)
PLATFORM_UI_ZONE = 0.83  # below this share of the height, YouTube's own buttons and title sit
SIDE_MARGIN = 60
FRAME_SECONDS = 1 / 30

FONTS = {
    "Montserrat ExtraBold": "Montserrat-ExtraBold.ttf",
    "Montserrat Black": "Montserrat-Black.ttf",
    "Anton": "Anton-Regular.ttf",
    "Poppins SemiBold": "Poppins-SemiBold.ttf",
}
# average glyph width as a share of the caption size, used only if a font file is missing
# (capitals / mixed case; measured from the fonts with the renderer's sizing rule)
_WIDTH = {"Montserrat ExtraBold": (0.50, 0.41), "Montserrat Black": (0.52, 0.43),
          "Anton": (0.29, 0.26), "Poppins SemiBold": (0.40, 0.33)}

PresetName = Literal["viral", "bold", "minimal", "gaming"]


@dataclass(frozen=True)
class Style:
    font: str
    size: int
    text_color: str  # "#RRGGBB"
    highlight_color: str
    emphasis_color: str | None  # gaming: shouted words
    outline: int
    shadow: int
    position: int  # centre of the caption, percent down the frame
    uppercase: bool
    words_per_line: int
    animation: Literal["pop", "none"]
    speed: float  # animation speed: 1 = normal, 2 = twice as fast


PRESETS: dict[str, dict[str, Any]] = {
    # Matches the reference Short: heavy capitals, white with a thick black outline,
    # two to four words a line, the spoken word in yellow, about three quarters down.
    "viral": dict(font="Montserrat ExtraBold", size=88, text_color="#FFFFFF", highlight_color="#FFE11A",
                  emphasis_color=None, outline=7, shadow=3, position=76, uppercase=True,
                  words_per_line=3, animation="pop", speed=1.0),
    "bold": dict(font="Anton", size=116, text_color="#FFFFFF", highlight_color="#5BFF4A",
                 emphasis_color=None, outline=8, shadow=4, position=72, uppercase=True,
                 words_per_line=2, animation="pop", speed=1.0),
    "minimal": dict(font="Poppins SemiBold", size=62, text_color="#FFFFFF", highlight_color="#FFFFFF",
                    emphasis_color=None, outline=0, shadow=3, position=80, uppercase=False,
                    words_per_line=6, animation="none", speed=1.0),
    "gaming": dict(font="Montserrat Black", size=92, text_color="#FFFFFF", highlight_color="#FFE11A",
                   emphasis_color="#FF3B30", outline=7, shadow=3, position=74, uppercase=True,
                   words_per_line=3, animation="pop", speed=1.3),
}
PRESET_LABELS = {
    "viral": "Viral Highlight", "bold": "Bold Emphasis", "minimal": "Minimal Professional", "gaming": "Gaming",
}


class CaptionStyle(BaseModel):
    """A preset plus any of its values the owner has changed. Empty fields use the preset."""

    preset: PresetName = "viral"
    font: str | None = None
    size: int | None = Field(default=None, ge=36, le=160)
    text_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: int | None = Field(default=None, ge=0, le=14)
    shadow: int | None = Field(default=None, ge=0, le=10)
    position: int | None = Field(default=None, ge=45, le=83)
    uppercase: bool | None = None
    words_per_line: int | None = Field(default=None, ge=1, le=8)
    speed: float | None = Field(default=None, ge=0.5, le=2.5)
    enabled: bool = True

    @field_validator("font")
    @classmethod
    def _known_font(cls, value: str | None) -> str | None:
        if value is not None and value not in FONTS:
            raise ValueError(f"Unknown font: {value}. Available: {', '.join(FONTS)}")
        return value

    def resolve(self) -> Style:
        base = dict(PRESETS[self.preset])
        for key in ("font", "size", "text_color", "highlight_color", "outline", "shadow", "position",
                    "uppercase", "words_per_line", "speed"):
            value = getattr(self, key)
            if value is not None:
                base[key] = value
        return Style(**base)


# ---- words onto the clip's clock ----------------------------------------------------


@dataclass
class CWord:
    s: float  # seconds from the start of the clip
    e: float
    text: str
    p: float = 1.0
    piece: int = 0  # which kept range of the clip it came from


def clip_words(words: list[dict[str, Any]], segments: list[list[float]] | list[tuple[float, float]]) -> list[CWord]:
    """Words that fall inside the clip's kept ranges, timed from the start of the clip.

    `words` are {"s", "e", "w", "p"} on the video's clock. A word that straddles
    the edge of a kept range is clipped to it (the cutting engine never does
    this, but a hand-edited range might).
    """
    out: list[CWord] = []
    offset = 0.0
    for n, (a, b) in enumerate(segments):
        for w in words:
            ws, we = float(w["s"]), float(w["e"])
            if we <= a or ws >= b:
                continue
            s, e = max(ws, a), min(we, b)
            text = str(w["w"]).strip()
            if text:
                out.append(CWord(round(offset + s - a, 3), round(offset + e - a, 3), text,
                                 float(w.get("p", 1.0) if w.get("p") is not None else 1.0), n))
        offset += b - a
    out.sort(key=lambda w: (w.s, w.e))
    return out


# ---- lines --------------------------------------------------------------------------


@dataclass
class Line:
    words: list[CWord]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _shown(text: str, style: Style) -> str:
    return text.upper() if style.uppercase else text


@lru_cache(maxsize=8)
def _metrics(font_file: str) -> tuple[int, dict[int, int], int] | None:
    """(line height in font units, advance width per character, fallback width), read from a TrueType file.

    The renderer (libass, copying Windows) scales a font so that its full line
    height, winAscent + winDescent, equals the caption size. So widths are
    measured against that height, not against the em square: for Montserrat
    that makes the letters 36% narrower than a naive reading of the size.
    Found by the frame-by-frame check, where highlights sat nearer the centre
    than predicted.
    """
    path = FONTS_DIR / font_file
    try:
        data = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, offset, _ = struct.unpack(">4sLLL", data[12 + 16 * i:28 + 16 * i])
            tables[tag] = offset
        units = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        if b"OS/2" in tables:
            win_ascent, win_descent = struct.unpack(">HH", data[tables[b"OS/2"] + 74:tables[b"OS/2"] + 78])
            height = (win_ascent + win_descent) or units
        else:
            height = units
        n_metrics = struct.unpack(">H", data[tables[b"hhea"] + 34:tables[b"hhea"] + 36])[0]
        hmtx = tables[b"hmtx"]
        advances = [struct.unpack(">H", data[hmtx + 4 * k:hmtx + 4 * k + 2])[0] for k in range(n_metrics)]

        cmap = tables[b"cmap"]
        sub = None
        for k in range(struct.unpack(">H", data[cmap + 2:cmap + 4])[0]):
            platform, encoding, off = struct.unpack(">HHL", data[cmap + 4 + 8 * k:cmap + 12 + 8 * k])
            if (platform, encoding) == (3, 1) and struct.unpack(">H", data[cmap + off:cmap + off + 2])[0] == 4:
                sub = cmap + off
        if sub is None:
            return None
        segs = struct.unpack(">H", data[sub + 6:sub + 8])[0] // 2
        ends = struct.unpack(f">{segs}H", data[sub + 14:sub + 14 + 2 * segs])
        starts_at = sub + 16 + 2 * segs
        starts = struct.unpack(f">{segs}H", data[starts_at:starts_at + 2 * segs])
        deltas = struct.unpack(f">{segs}h", data[starts_at + 2 * segs:starts_at + 4 * segs])
        ranges_at = starts_at + 4 * segs
        widths: dict[int, int] = {}
        for k in range(segs):
            range_offset = struct.unpack(">H", data[ranges_at + 2 * k:ranges_at + 2 * k + 2])[0]
            for code in range(starts[k], min(ends[k], 0x24FF) + 1):  # Latin, punctuation, common symbols
                if range_offset == 0:
                    glyph = (code + deltas[k]) & 0xFFFF
                else:
                    at = ranges_at + 2 * k + range_offset + 2 * (code - starts[k])
                    glyph = struct.unpack(">H", data[at:at + 2])[0]
                    glyph = (glyph + deltas[k]) & 0xFFFF if glyph else 0
                if glyph:
                    widths[code] = advances[min(glyph, n_metrics - 1)]
        fallback = widths.get(ord("M"), units)
        return height, widths, fallback
    except (OSError, KeyError, struct.error, IndexError):
        return None


def text_width(text: str, style: Style) -> float:
    """Width of a line in frame pixels, from the font's own glyph widths, outline included."""
    metrics = _metrics(FONTS.get(style.font, ""))
    if metrics is None:  # font file missing: a cautious estimate
        caps, mixed = _WIDTH.get(style.font, (0.75, 0.6))
        return len(text) * style.size * (caps if style.uppercase else mixed) + 2 * style.outline
    height, widths, fallback = metrics
    advance = sum(widths.get(ord(ch), fallback) for ch in text)
    return advance * style.size / height + 2 * style.outline


def _ends_thought(text: str) -> bool:
    return text.rstrip("\"'”’)]").endswith((".", "?", "!", "…", ",", ";", ":"))


def build_lines(words: list[CWord], style: Style, clip_length: float) -> list[Line]:
    """Group words into short caption lines that fit the frame width.

    A new line starts when the line is full, the next word would not fit, the
    speaker pauses, a sentence or clause ends, or the clip jumps over removed
    dead air.
    """
    room = FRAME_W - 2 * SIDE_MARGIN
    lines: list[list[CWord]] = []
    for w in words:
        current = lines[-1] if lines else None
        if current:
            prev = current[-1]
            candidate = " ".join(x.text for x in current + [w])
            new_line = (
                len(current) >= style.words_per_line
                or text_width(_shown(candidate, style), style) > room
                or w.s - prev.e >= LINE_BREAK_PAUSE
                or _ends_thought(prev.text)
                or w.piece != prev.piece
            )
            if not new_line:
                current.append(w)
                continue
        lines.append([w])

    out: list[Line] = []
    for k, group in enumerate(lines):
        start = group[0].s
        last_end = group[-1].e
        next_start = lines[k + 1][0].s if k + 1 < len(lines) else math.inf
        end = min(last_end + HOLD, next_start, clip_length)
        out.append(Line(group, round(start, 3), round(max(end, last_end), 3)))
    return out


# ---- ASS ----------------------------------------------------------------------------


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    # braces start override tags and a backslash starts an escape in ASS
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")")


def _shouted(line: Line) -> bool:
    return line.text.rstrip("\"'”’)]").endswith("!")


LABEL_STYLE = Style(font="Montserrat Black", size=64, text_color="#FFFFFF", highlight_color="#FFFFFF",
                    emphasis_color=None, outline=6, shadow=3, position=9, uppercase=True, words_per_line=2,
                    animation="none", speed=1.0)


def label_size(text: str) -> tuple[float, float]:
    """Width and height in 1080x1920 pixels of the PART label, outline included."""
    return text_width(text.upper(), LABEL_STYLE) + 2 * LABEL_STYLE.outline, LABEL_STYLE.size * 1.25


def part_label_text(part: int, parts: int) -> str:
    return f"PART {part}/{parts}" if parts > 1 else ""


TAG_STYLE = Style(font="Montserrat ExtraBold", size=36, text_color="#FFFFFF", highlight_color="#FFFFFF",
                  emphasis_color=None, outline=3, shadow=1, position=6, uppercase=False, words_per_line=4,
                  animation="none", speed=1.0)


def credit_tag_text(creator: str | None) -> str:
    name = " ".join((creator or "").split())
    if not name:
        return ""
    return name if name.startswith("@") else "@" + name.replace(" ", "")


def tag_size(text: str) -> tuple[float, float]:
    """Width and height in 1080x1920 pixels of the creator credit, outline included."""
    return text_width(text, TAG_STYLE) + 2 * TAG_STYLE.outline, TAG_STYLE.size * 1.25


def to_ass(lines: list[Line], style: Style, label: dict[str, Any] | None = None,
           tag: dict[str, Any] | None = None, length: float | None = None) -> str:
    y = round(FRAME_H * style.position / 100)
    pop = style.animation == "pop"
    grow_ms = max(20, round(70 / style.speed))
    settle_ms = max(40, round(140 / style.speed))
    shadow_alpha = 0x60
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {FRAME_W}",
        f"PlayResY: {FRAME_H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{style.font},{style.size},{_ass_colour(style.text_color)},{_ass_colour(style.text_color)},"
        f"&H00000000,{_ass_colour('#000000', shadow_alpha)},0,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},"
        f"5,{SIDE_MARGIN},{SIDE_MARGIN},0,1",
        f"Style: Label,{LABEL_STYLE.font},{LABEL_STYLE.size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"{_ass_colour('#000000', shadow_alpha)},0,0,0,0,100,100,2,0,1,{LABEL_STYLE.outline},{LABEL_STYLE.shadow},"
        "5,0,0,0,1",
        f"Style: Tag,{TAG_STYLE.font},{TAG_STYLE.size},&H22FFFFFF,&H22FFFFFF,&H00000000,"
        f"{_ass_colour('#000000', shadow_alpha)},0,0,0,0,100,100,0,0,1,{TAG_STYLE.outline},{TAG_STYLE.shadow},"
        "5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    events: list[str] = []
    for line in lines:
        hot = (style.emphasis_color if style.emphasis_color and _shouted(line) else style.highlight_color)
        for k, w in enumerate(line.words):
            start = line.start if k == 0 else w.s
            end = line.words[k + 1].s if k + 1 < len(line.words) else line.end
            if end - start < 0.01:
                continue
            parts = []
            for m, other in enumerate(line.words):
                word = _escape(_shown(other.text, style))
                if m == k:
                    parts.append(f"{{\\c{_ass_colour(hot)}}}{word}{{\\c{_ass_colour(style.text_color)}}}")
                else:
                    parts.append(word)
            tags = f"\\pos({FRAME_W // 2},{y})\\q2"
            if pop and k == 0:
                scale = 112 if hot == style.highlight_color else 120
                tags += (f"\\fscx{scale}\\fscy{scale}\\t(0,{grow_ms},\\fscx104\\fscy104)"
                         f"\\t({grow_ms},{grow_ms + settle_ms},\\fscx100\\fscy100)")
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{{{tags}}}{' '.join(parts)}")
    if label and label.get("text"):
        until = float(label.get("until", 2.5))
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(until)},Label,,0,0,0,,"
            f"{{\\pos({int(label['x'])},{int(label['y'])})\\fad(150,300)}}{_escape(label['text'].upper())}"
        )
    if tag and tag.get("text") and length:
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(length)},Tag,,0,0,0,,"
            f"{{\\pos({int(tag['x'])},{int(tag['y'])})}}{_escape(tag['text'])}"
        )
    return header + "\n" + "\n".join(events) + "\n"


# ---- checks -------------------------------------------------------------------------


@dataclass
class CaptionCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class CaptionResult:
    ass: str
    lines: list[dict[str, Any]]
    checks: list[CaptionCheck]
    flagged: list[dict[str, Any]] = field(default_factory=list)
    word_count: int = 0

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    def report(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "word_count": self.word_count,
            "line_count": len(self.lines),
            "checks": [asdict(c) for c in self.checks],
            "flagged_words": self.flagged,
        }


def check_lines(lines: list[Line], words: list[CWord], style: Style, clip_length: float) -> list[CaptionCheck]:
    checks: list[CaptionCheck] = []
    if not words:
        return [CaptionCheck("speech", True, "No speech in this clip, so there are no captions.")]

    first, last = words[0], words[-1]
    first_line, last_line = lines[0], lines[-1]
    checks.append(CaptionCheck(
        "first_caption_on_time", abs(first_line.start - first.s) <= FRAME_SECONDS,
        f"First caption at {first_line.start:.2f} s, first word at {first.s:.2f} s.",
    ))
    checks.append(CaptionCheck(
        "last_word_shown_in_full", last_line.end >= last.e - 0.005 and last_line.end <= clip_length + 0.005,
        f"Last word ends at {last.e:.2f} s, its caption stays until {last_line.end:.2f} s, clip ends at "
        f"{clip_length:.2f} s.",
    ))
    early = [ln.text for ln in lines if ln.end < ln.words[-1].e - 0.005]
    checks.append(CaptionCheck(
        "no_caption_ends_before_its_words", not early,
        "Every line stays up until its last word has been spoken." if not early else f"Ends early: {early[:3]}",
    ))
    lingering = [ln.text for ln in lines if ln.end - ln.words[-1].e > HOLD + 0.005]
    checks.append(CaptionCheck(
        "no_caption_lingers", not lingering,
        f"No line stays up more than {HOLD:.1f} s after its last word." if not lingering else f"Lingers: {lingering[:3]}",
    ))
    shown = [w.text for ln in lines for w in ln.words]
    checks.append(CaptionCheck(
        "every_word_once", shown == [w.text for w in words],
        f"{len(shown)} caption words for {len(words)} spoken words, in order.",
    ))
    top = FRAME_H * style.position / 100 - style.size * 0.7
    bottom = FRAME_H * style.position / 100 + style.size * 0.7
    checks.append(CaptionCheck(
        "clear_of_part_label", top >= FRAME_H * LABEL_ZONE,
        f"Caption top at {top / FRAME_H:.0%} of the height; the part label area ends at {LABEL_ZONE:.0%}.",
    ))
    checks.append(CaptionCheck(
        "clear_of_platform_buttons", bottom <= FRAME_H * PLATFORM_UI_ZONE,
        f"Caption bottom at {bottom / FRAME_H:.0%} of the height; YouTube's own title and buttons start "
        f"around {PLATFORM_UI_ZONE:.0%}.",
    ))
    widest = max(lines, key=lambda ln: text_width(_shown(ln.text, style), style))
    width = text_width(_shown(widest.text, style), style)
    measured = _metrics(FONTS.get(style.font, "")) is not None
    checks.append(CaptionCheck(
        "fits_the_width", width <= FRAME_W - 2 * SIDE_MARGIN or len(widest.words) == 1,
        f"Widest line {width:.0f} px of {FRAME_W - 2 * SIDE_MARGIN} px available"
        + ("" if measured else " (estimated: font file not found)") + ".",
    ))
    return checks


def build_captions(
    words: list[dict[str, Any]], segments: list[list[float]], clip_length: float, style_in: CaptionStyle,
    position: int | None = None, label: dict[str, Any] | None = None, tag: dict[str, Any] | None = None,
) -> CaptionResult:
    """Captions for one clip. `position` overrides the style's height (framing moves it off faces);
    `label` is the PART label to show over the opening seconds ({text, x, y, until}); `tag` is the
    creator credit shown for the whole clip ({text, x, y})."""
    style = style_in.resolve()
    if position is not None:
        style = replace(style, position=int(position))
    cwords = clip_words(words, segments)
    if not style_in.enabled:
        extras = label or tag
        return CaptionResult(ass=to_ass([], style, label, tag, clip_length) if extras else "", lines=[],
                             checks=[CaptionCheck("captions", True, "Captions are turned off.")],
                             word_count=len(cwords))
    lines = build_lines(cwords, style, clip_length)
    flagged = [
        {"t": round(w.s, 2), "word": w.text, "confidence": round(w.p, 2)}
        for w in cwords if w.p < LOW_CONFIDENCE
    ]
    return CaptionResult(
        ass=to_ass(lines, style, label, tag, clip_length) if lines or label or tag else "",
        lines=[
            {"start": ln.start, "end": ln.end, "text": _shown(ln.text, style),
             "words": [{"s": w.s, "e": w.e, "w": w.text, "p": round(w.p, 2)} for w in ln.words]}
            for ln in lines
        ],
        checks=check_lines(lines, cwords, style, clip_length),
        flagged=flagged,
        word_count=len(cwords),
    )


def highlighted_at(lines: list[dict[str, Any]], t: float) -> str | None:
    """Which word is highlighted at time t (seconds into the clip), by the same rule the ASS uses."""
    for ln in lines:
        if ln["start"] <= t < ln["end"]:
            words = ln["words"]
            current = words[0]
            for w in words:
                if w["s"] <= t:
                    current = w
            return current["w"]
    return None
