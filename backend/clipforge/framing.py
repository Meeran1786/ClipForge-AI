"""Vertical framing: finding faces, choosing a layout, and where the 9:16 camera looks.

Everything here works in two coordinate spaces:

* source pixels (the original video, for example 1920x1080), and
* output pixels of a 1080x1920 Short (previews are the same picture scaled down).

A clip's frames are sampled a few times a second and faces found with OpenCV's
YuNet model. From those samples the clip is classed as having a facecam (a
small face that stays in one off-centre place, as in a gaming stream), a
speaker (a face worth following), or neither. That picks the layout, the
camera path follows the face smoothly, and a slow push-in lands on the key
moment. The same face positions, carried into output pixels, decide where the
captions and the PART label go so that they never cover a face.

No AI is called. The face model runs on this computer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .captions import SIDE_MARGIN as CAPTION_SIDE
from .config import Settings
from .media import read_frames

OUT_W, OUT_H = 1080, 1920
SAMPLE_FPS = 5.0
DETECT_WIDTH = 1280
MIN_FACE_PX = 10  # at detection size: anything smaller is noise
FACE_SCORE = 0.7
FACECAM_SHARE = 0.35  # a facecam has a face in it at least this share of the time

LAYOUTS = ("full", "facecam_inset", "reaction", "split")
LAYOUT_LABELS = {
    "full": "Full screen",
    "facecam_inset": "Gameplay with facecam",
    "reaction": "Facecam reaction",
    "split": "Split screen",
}
NEEDS_FACECAM = {"facecam_inset", "split"}

SPLIT_TOP = 0.40  # share of the height given to the streamer in split screen
CAM_SHARPEN_FROM = 1.8  # sharpen the facecam when it is enlarged more than this
CAM_SHARPEN = 0.6  # how strongly
MAX_GAME_SHIFT = 0.15  # split screen: how far (share of the width) the gameplay may slide away from the facecam
INSET_W, INSET_TOP, INSET_MARGIN = 440, 230, 40
REACTION_CENTRE = 0.40  # where the facecam sits in the reaction layout, as a share of the height
TIGHT_ZOOM = 1.6  # reaction layout without a facecam: closer on the speaker's face

ZOOM_MAX = 1.15
ZOOM_LEAD, ZOOM_RISE, ZOOM_HOLD, ZOOM_FALL = 0.4, 0.8, 1.5, 1.2  # seconds
MAX_PAN = 0.35  # the camera moves at most this many crop-widths a second
SMOOTH_SIGMA = 3.0  # in samples (5 a second): about 0.6 s
JUMP_SHARE = 0.5  # the subject moving this many crop-widths between samples is treated as a cut
MIN_FOLLOW_SHARE = 0.05  # faces shorter than this share of the height (people in the background) are not followed

LABEL_SECONDS = 2.5
# Tried in this order after the style's own height when a face is in the way: close to the middle
# first, never into YouTube's title and channel name at the bottom.
CAPTION_CANDIDATES = (54, 66, 48, 70, 42, 36, 30)
FACE_OVERLAP = 0.15  # share of a face that may be covered before it counts as covered
COVER_LIMIT = 0.0  # share of sampled frames a caption may cover a face in (if no height is clear, the least is used)
TEXT_MARGIN = 0.10  # faces are given this much room (share of their size, each side) when placing text


# ---- boxes ---------------------------------------------------------------------------


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x1 and self.y <= y <= self.y1

    def overlap(self, other: "Box") -> float:
        w = min(self.x1, other.x1) - max(self.x, other.x)
        h = min(self.y1, other.y1) - max(self.y, other.y)
        return max(0.0, w) * max(0.0, h)

    def inside(self, W: float, H: float) -> "Box":
        """The same size where possible, moved (then shrunk if needed) to lie within W x H."""
        w, h = min(self.w, W), min(self.h, H)
        x = min(max(self.x, 0.0), W - w)
        y = min(max(self.y, 0.0), H - h)
        return Box(x, y, w, h)

    def with_aspect(self, aspect: float) -> "Box":
        """Grow the shorter side about the centre so that w / h == aspect."""
        if self.w / self.h < aspect:
            w = self.h * aspect
            return Box(self.cx - w / 2, self.y, w, self.h)
        h = self.w / aspect
        return Box(self.x, self.cy - h / 2, self.w, h)

    def within_aspect(self, aspect: float) -> "Box":
        """The largest box of that aspect inside this one, about the centre."""
        if self.w / self.h > aspect:
            w = self.h * aspect
            return Box(self.cx - w / 2, self.y, w, self.h)
        h = self.w / aspect
        return Box(self.x, self.cy - h / 2, self.w, h)

    def zoomed(self, factor: float) -> "Box":
        w, h = self.w / factor, self.h / factor
        return Box(self.cx - w / 2, self.cy - h / 2, w, h)

    def scaled(self, k: float) -> "Box":
        return Box(self.x * k, self.y * k, self.w * k, self.h * k)

    def to_list(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.w, 1), round(self.h, 1)]

    @staticmethod
    def of(values: list[float] | tuple[float, ...]) -> "Box":
        return Box(*[float(v) for v in values[:4]])


@dataclass
class Sample:
    t: float  # seconds into the clip
    faces: list[Box]


def samples_to_json(samples: list[Sample]) -> list[list[Any]]:
    return [[s.t, [f.to_list() for f in s.faces]] for s in samples]


def samples_from_json(data: list[list[Any]]) -> list[Sample]:
    return [Sample(float(t), [Box.of(f) for f in faces]) for t, faces in data]


# ---- finding faces ------------------------------------------------------------------------


class FaceDetector:
    """OpenCV's YuNet face detector."""

    def __init__(self, model: Path, score: float = FACE_SCORE) -> None:
        import cv2

        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except AttributeError:
            pass
        self._det = cv2.FaceDetectorYN.create(str(model), "", (320, 320), score, 0.3, 50)

    def detect(self, frame: np.ndarray) -> list[tuple[float, float, float, float]]:
        h, w = frame.shape[:2]
        self._det.setInputSize((w, h))
        _, out = self._det.detect(frame)
        if out is None:
            return []
        return [(float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in out if r[3] >= MIN_FACE_PX]


def make_detector(settings: Settings) -> FaceDetector | None:
    """The face detector, or None when its model file is not installed (framing then keeps the centre)."""
    if not Path(settings.face_model).is_file():
        return None
    return FaceDetector(Path(settings.face_model))


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def scan_faces(
    settings: Settings, detector, video: Path, src_size: tuple[int, int],
    segments: list[tuple[float, float]], check: Callable[[], None] | None = None,
) -> list[Sample]:
    """Faces in the clip, a few times a second, in source pixels, timed on the clip's clock."""
    W, H = src_size
    dw = _even(min(DETECT_WIDTH, W))
    dh = _even(dw * H / W)
    scale = W / dw
    samples: list[Sample] = []
    offset = 0.0
    for a, b in segments:
        count = max(1, int((b - a) * SAMPLE_FPS))
        for k, frame in enumerate(read_frames(settings, video, a, b, SAMPLE_FPS, (dw, dh))):
            if k >= count:
                break
            faces = [Box(x * scale, y * scale, w * scale, h * scale) for x, y, w, h in detector.detect(frame)]
            samples.append(Sample(round(offset + k / SAMPLE_FPS, 3), faces))
            if check and k % 10 == 0:
                check()
        offset += b - a
    return samples


def spread_frames(
    settings: Settings, video: Path, src_size: tuple[int, int], ranges: list[tuple[float, float]], count: int = 30,
) -> list[np.ndarray]:
    """About `count` frames spread over the given stretches of video, at detection size."""
    W, H = src_size
    dw = _even(min(DETECT_WIDTH, W))
    size = (dw, _even(dw * H / W))
    total = sum(b - a for a, b in ranges)
    if total <= 0:
        return []
    fps = max(count / total, 0.05)
    frames: list[np.ndarray] = []
    for a, b in ranges:
        frames += list(read_frames(settings, video, a, b, fps, size))
        if len(frames) >= count:
            break
    return frames[:count]


MOTION_EDGE = 8.0  # the picture must move this much more outside a line than inside for it to count as the border
BAR_DARK = 5  # a row this dark (0-255) across most of its width, in nearly every frame, is black bar
BAR_MIN, BAR_MAX = 0.015, 0.16  # a bar thinner than this is noise; thicker is a dark scene, not a bar


def letterbox(frames: list[np.ndarray], H: float) -> tuple[float, float]:
    """Cinema black bars (a cutscene's letterbox) at the top and bottom, in source pixels; (0, 0) when none.

    A row belongs to a bar when it is black across most of its width in nearly every frame. Overlays
    drawn over the bar (a facecam, chat, a timer) cover only part of a row, so they do not hide it.
    """
    import cv2

    if len(frames) < 3:
        return 0.0, 0.0
    fh = frames[0].shape[0]
    level = np.stack([np.percentile(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 55, axis=1) for f in frames])
    # a bar is black in (nearly) every frame; a dark scene is dark only some of the time
    rows = np.percentile(level, 80, axis=0) < BAR_DARK

    def run(seq) -> int:
        n = 0
        for v in seq:
            if not v:
                break
            n += 1
        return n

    out = []
    for n in (run(rows), run(rows[::-1])):
        share = n / fh
        out.append(round((n + 1) * H / fh, 1) if BAR_MIN <= share <= BAR_MAX else 0.0)
    return out[0], out[1]


# ---- what kind of picture this is ------------------------------------------------------


@dataclass
class Subject:
    kind: str  # facecam | face | none
    facecam: Box | None = None
    share: float = 0.0  # share of sampled frames the subject was seen in
    note: str = ""
    core: Box | None = None  # facecam: where its faces actually were (the overlay contains at least this)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "facecam": self.facecam.to_list() if self.facecam else None,
                "share": round(self.share, 2), "note": self.note}


def find_facecam(samples: list[Sample], W: float, H: float) -> Subject | None:
    """A facecam: small faces that keep to one off-centre region of the frame, or None.

    A streamer's facecam stays in one place for the whole video, so this is
    best given the samples of every clip together; one short clip can be
    dominated by a face in the gameplay.
    """
    n = max(len(samples), 1)
    entries = [(i, f) for i, s in enumerate(samples) for f in s.faces]
    if not entries:
        return None
    cx = np.array([f.cx for _, f in entries])
    cy = np.array([f.cy for _, f in entries])

    # A facecam is a cluster of small faces that keeps to one off-centre region.
    best_frames: set[int] = set()
    best_members = np.array([0])
    step = max(1, len(entries) // 400)  # enough candidate centres to find the cluster, without N² work
    for _, f in entries[::step]:
        members = np.nonzero((np.abs(cx - f.cx) <= 0.12 * W) & (np.abs(cy - f.cy) <= 0.16 * H))[0]
        frames = {entries[m][0] for m in members}
        if len(frames) > len(best_frames):
            best_frames, best_members = frames, members
    cluster = [entries[m][1] for m in best_members]
    share = len(best_frames) / n
    face_w = float(np.median([f.w for f in cluster]))
    face_h = float(np.median([f.h for f in cluster]))
    mx, my = float(np.median([f.cx for f in cluster])), float(np.median([f.cy for f in cluster]))
    off_centre = abs(mx - W / 2) > 0.2 * W or abs(my - H / 2) > 0.22 * H
    if not (share >= FACECAM_SHARE and face_h / H <= 0.3 and off_centre):
        return None
    x0 = float(np.percentile([f.x for f in cluster], 5))
    y0 = float(np.percentile([f.y for f in cluster], 5))
    x1 = float(np.percentile([f.x1 for f in cluster], 95))
    y1 = float(np.percentile([f.y1 for f in cluster], 95))
    # head and shoulders: a facecam shows about three face widths across
    cam = Box(x0 - 1.0 * face_w, y0 - 0.7 * face_h, (x1 - x0) + 2.0 * face_w, (y1 - y0) + 2.0 * face_h)
    cam = cam.inside(W, H)
    side = "left" if mx < W / 3 else "right" if mx > 2 * W / 3 else "middle"
    level = "top" if my < H / 3 else "bottom" if my > 2 * H / 3 else "middle"
    return Subject("facecam", cam, share, f"A facecam was found at the {level} {side}, "
                                          f"with a face in it {share:.0%} of the time.",
                   core=Box(x0, y0, x1 - x0, y1 - y0))


def snap_to_overlay(
    frames: list[np.ndarray], box: Box, W: float, H: float, core: Box | None = None,
) -> tuple[Box, list[str]]:
    """Move each side of a facecam box onto the overlay's real edge, where one can be seen.

    A facecam is pasted over the game, so its border is a straight line that
    stays put while the picture changes on both sides of it. Averaged over many
    frames, that line stands out from everything that moves. `core` is where
    the faces were: the overlay holds at least that much, so each edge is looked
    for between the faces and a little beyond the estimate. A side with no clear
    line keeps its estimate. Returns the box and the sides that snapped.
    """
    import cv2

    if len(frames) < 4:
        return box, []
    fh, fw = frames[0].shape[:2]
    k = fw / W
    gx = np.zeros((fh, fw), np.float32)
    gy = np.zeros((fh, fw), np.float32)
    s1 = np.zeros((fh, fw), np.float32)
    s2 = np.zeros((fh, fw), np.float32)
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx += np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
        gy += np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        s1 += g
        s2 += g * g
    gx /= len(frames)
    gy /= len(frames)
    # how much each pixel changes over time: the game moves, a facecam's room mostly does not
    motion = np.sqrt(np.maximum(s2 / len(frames) - (s1 / len(frames)) ** 2, 0))
    b = box.scaled(k)
    x0, y0, x1, y1 = b.x, b.y, b.x1, b.y1
    reach_x, reach_y = 0.3 * b.w, 0.3 * b.h
    snapped: list[str] = []

    def candidates(profile: np.ndarray, lo: float, hi: float) -> list[int]:
        """Clear lines in the window: local peaks at least 3x the window's median, strongest first."""
        lo_i, hi_i = max(int(lo), 1), min(int(hi), len(profile) - 2)
        if hi_i - lo_i < 3:
            return []
        window = profile[lo_i:hi_i]
        bar = 3.0 * max(float(np.median(window)), 1e-3)
        peaks = [i for i in range(len(window)) if window[i] >= bar
                 and window[i] >= window[max(i - 2, 0):i + 3].max()]
        return [lo_i + i for i in sorted(peaks, key=lambda i: -window[i])][:8]

    # the profiles are taken along the stretch the faces cover, which is surely inside the overlay
    inner = core.scaled(k) if core is not None else b
    rows = slice(max(int(inner.y), 0), max(min(int(inner.y1), fh), int(inner.y) + 1))
    cols = slice(max(int(inner.x), 0), max(min(int(inner.x1), fw), int(inner.x) + 1))
    col_profile = gx[rows].mean(axis=0)
    row_profile = gy[:, cols].mean(axis=1)
    col_motion = motion[rows].mean(axis=0)
    row_motion = motion[:, cols].mean(axis=1)
    band = max(3, round(0.012 * fw))

    def contrast(side: str, e: int) -> float:
        """How much more the picture moves just outside this line than just inside it."""
        m = col_motion if side in ("left", "right") else row_motion
        before, after = m[max(e - band, 0):e], m[e + 1:e + 1 + band]
        if not len(before) or not len(after):
            return 0.0
        outside, inside = (before, after) if side in ("left", "top") else (after, before)
        return float(outside.mean() - inside.mean())

    def choose(side: str, lo: float, hi: float) -> int | None:
        """The overlay's edge: of the clear lines, the one where still picture meets moving game.

        A static line in the game (a quest list, a scoreboard) can be stronger than the overlay's
        border; the border is the line with the picture changing on one side and not the other.
        """
        found = candidates(col_profile if side in ("left", "right") else row_profile, lo, hi)
        if not found:
            return None
        scored = [(contrast(side, e), e) for e in found]
        best_score, best = max(scored)
        return best if best_score >= MOTION_EDGE else found[0]

    mx, my = 0.25 * inner.w, 0.25 * inner.h
    if core is not None:
        # The estimate from face sizes can be far off (a webcam often shows a whole room), so look well past it.
        # Never inside the faces, though: a headset, hood or hairline is a steady line too, and snapping
        # to it crops the streamer down to a blurry patch of face.
        reach_x, reach_y = 0.8 * b.w, 0.8 * b.h
        gx_, gy_ = 0.15 * inner.w, 0.15 * inner.h
        windows = {"left": (x0 - reach_x, inner.x - gx_), "right": (inner.x1 + gx_, x1 + reach_x),
                   "top": (y0 - reach_y, inner.y - gy_), "bottom": (inner.y1 + gy_, y1 + reach_y)}
    else:
        windows = {"left": (x0 - reach_x, x0 + reach_x), "right": (x1 - reach_x, x1 + reach_x),
                   "top": (y0 - reach_y, y0 + reach_y), "bottom": (y1 - reach_y, y1 + reach_y)}
    edges = {side: (choose(side, lo, hi), None) for side, (lo, hi) in windows.items()}
    out = dict(left=x0, right=x1, top=y0, bottom=y1)
    for side, (found, _old) in edges.items():
        if found is None:
            continue
        # the line itself (and any border drawn on it) belongs to neither side: step just inside it
        out[side] = found + (3 if side in ("left", "top") else -2)
        snapped.append(side)
    if core is not None:
        # the faces seen must still be (nearly) inside, or the "edge" is something else
        believable = (out["left"] <= inner.x + mx and out["right"] >= inner.x1 - mx
                      and out["top"] <= inner.y + my and out["bottom"] >= inner.y1 - my)
    else:  # without faces to go by, refuse to shrink the box to a sliver
        believable = out["right"] - out["left"] >= 0.5 * b.w and out["bottom"] - out["top"] >= 0.5 * b.h
    if not snapped or not believable:
        return box, []
    return Box(out["left"] / k, out["top"] / k, (out["right"] - out["left"]) / k,
               (out["bottom"] - out["top"]) / k).inside(W, H), snapped


def classify(samples: list[Sample], W: float, H: float, facecam: Box | None = None,
             video_checked: bool = False) -> Subject:
    """Facecam, a speaker to follow, or neither.

    `facecam` is one already found across the whole video. With `video_checked`
    the whole video was looked at and has none, so a single clip does not go
    looking for one: someone standing off to one side for a while is not a
    facecam.
    """
    n = max(len(samples), 1)
    if facecam is not None:
        inside = sum(1 for s in samples if any(facecam.contains(f.cx, f.cy) for f in s.faces)) / n
        return Subject("facecam", facecam, inside,
                       f"The video's facecam is used; a face shows in it in {inside:.0%} of this clip.")
    if not any(s.faces for s in samples):
        return Subject("none", note="No face was found in this clip, so the centre of the picture is kept.")
    found = None if video_checked else find_facecam(samples, W, H)
    if found is not None:
        return found
    share_any = sum(1 for s in samples if s.faces) / n
    largest = [max(f.h for f in s.faces) / H for s in samples if s.faces]
    if share_any >= 0.3 and float(np.median(largest)) >= 0.08:
        return Subject("face", None, share_any, f"A speaker's face is visible in {share_any:.0%} of the clip.")
    return Subject("none", None, share_any,
                   "Faces were too small or too rare to follow, so the centre of the picture is kept.")


def choose_layout(subject: Subject, requested: str | None) -> tuple[str, str]:
    """The layout to use and why."""
    if requested and requested != "auto":
        if requested in NEEDS_FACECAM and subject.kind != "facecam":
            return "full", f"“{LAYOUT_LABELS[requested]}” needs a facecam and none was found, so full screen is used."
        if requested == "reaction" and subject.kind == "none":
            return "full", "“Facecam reaction” needs a face and none was found, so full screen is used."
        return requested, f"You chose “{LAYOUT_LABELS[requested]}”."
    if subject.kind == "facecam":
        return "split", "A facecam was found: the streamer goes on top and the gameplay below."
    if subject.kind == "face":
        return "full", "Full screen, following the speaker's face."
    return "full", "Full screen, keeping the centre of the picture."


# ---- the camera path ---------------------------------------------------------------------


def _fill(values: np.ndarray, default: float) -> np.ndarray:
    known = ~np.isnan(values)
    if not known.any():
        return np.full(len(values), default)
    idx = np.arange(len(values))
    return np.interp(idx, idx[known], values[known])


def _smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    if len(values) < 3:
        return values
    k = 5 if len(values) >= 5 else 3
    padded = np.pad(values, k // 2, mode="edge")
    med = np.array([np.median(padded[i:i + k]) for i in range(len(values))])  # drop single-frame jumps
    radius = int(3 * sigma)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    return np.convolve(np.pad(med, radius, mode="edge"), kernel, mode="valid")


def _limit_speed(values: np.ndarray, max_step: float) -> np.ndarray:
    out = values.copy()
    for i in range(1, len(out)):
        out[i] = out[i - 1] + float(np.clip(out[i] - out[i - 1], -max_step, max_step))
    return out


def camera_path(
    samples: list[Sample], W: float, H: float, follow: bool, cuts: list[float] | None = None
) -> dict[str, list[float]]:
    """Where the camera points over the clip (source pixels): smooth, never jittery, and free to jump at a cut.

    Each stretch between cuts is smoothed on its own, so the camera never drifts
    from one shot into the next; at a cut it jumps straight to the new subject.
    """
    ts = np.array([s.t for s in samples], dtype=float)
    if len(ts) == 0 or not follow:
        return {"t": [0.0], "x": [W / 2], "y": [H / 2], "cuts": []}
    crop_w = min(H * 9 / 16, W)
    xs = np.full(len(ts), np.nan)
    ys = np.full(len(ts), np.nan)
    prev: tuple[float, float] | None = None
    for i, s in enumerate(samples):
        target = follow_target(s.faces, H, crop_w, prev)
        if target is not None:
            xs[i], ys[i] = target
            prev = target
    # a big jump in where the subject is, between one sample and the next, is a cut the scene
    # detector missed (or a different person): jump with it rather than pan across
    implied = []
    known = np.nonzero(~np.isnan(xs))[0]
    for a, b in zip(known, known[1:]):
        if abs(xs[b] - xs[a]) > JUMP_SHARE * crop_w and not any(ts[a] < c <= ts[b] for c in cuts or []):
            implied.append(float(ts[b - 1] + ts[b]) / 2)
    all_cuts = sorted({round(c, 3) for c in [*(cuts or []), *implied] if 0 < c < ts[-1]})
    step = MAX_PAN * crop_w / SAMPLE_FPS
    out_x, out_y = np.empty_like(xs), np.empty_like(ys)
    bounds = sorted({0, len(ts), *[int(np.searchsorted(ts, c)) for c in all_cuts]})
    for a, b in zip(bounds, bounds[1:]):
        out_x[a:b] = _limit_speed(_smooth(_fill(xs[a:b], W / 2), SMOOTH_SIGMA), step)
        out_y[a:b] = _limit_speed(_smooth(_fill(ys[a:b], H / 2), SMOOTH_SIGMA), step)
    return {"t": [round(float(t), 3) for t in ts], "x": [round(float(x), 1) for x in out_x],
            "y": [round(float(y), 1) for y in out_y], "cuts": all_cuts}


def prominent(faces: list[Box], H: float) -> list[Box]:
    """The faces worth framing: not tiny, and not much smaller than the largest."""
    faces = [f for f in faces if f.h >= MIN_FOLLOW_SHARE * H]
    if not faces:
        return []
    top = max(f.area for f in faces)
    return [f for f in faces if f.area >= 0.5 * top]


def follow_target(faces: list[Box], H: float, crop_w: float, prev: tuple[float, float] | None
                  ) -> tuple[float, float] | None:
    """Where to point the camera for one sample: everyone worth framing if they fit, else one person.

    With two people who do not fit together, the camera stays with the one it
    was on, unless the other is clearly the bigger face.
    """
    main = prominent(faces, H)
    if not main:
        return None
    x0, x1 = min(f.x for f in main), max(f.x1 for f in main)
    if x1 - x0 <= 0.9 * crop_w:
        return (x0 + x1) / 2, float(np.mean([f.cy for f in main]))
    largest = max(main, key=lambda f: f.area)
    if prev is not None:
        nearest = min(main, key=lambda f: abs(f.cx - prev[0]))
        if largest.area < 2.0 * nearest.area:
            return nearest.cx, nearest.cy
    return largest.cx, largest.cy


def zoom_at(t: float, anchor: float | None) -> float:
    """A slow push-in on the key moment: up to 15% closer, held, then eased back."""
    if anchor is None:
        return 1.0
    start = anchor - ZOOM_LEAD
    if t < start:
        return 1.0

    def ease(x: float) -> float:
        x = min(max(x, 0.0), 1.0)
        return x * x * (3 - 2 * x)

    if t < start + ZOOM_RISE:
        return 1.0 + (ZOOM_MAX - 1.0) * ease((t - start) / ZOOM_RISE)
    if t < start + ZOOM_RISE + ZOOM_HOLD:
        return ZOOM_MAX
    return 1.0 + (ZOOM_MAX - 1.0) * (1 - ease((t - start - ZOOM_RISE - ZOOM_HOLD) / ZOOM_FALL))


# ---- layouts: what part of the source goes where in the Short ------------------------------


@dataclass
class FramePlan:
    layout: str
    src_w: int
    src_h: int
    path: dict[str, list[float]]
    facecam: Box | None = None
    anchor: float | None = None  # clip time of the key moment, for the zoom
    zoom: bool = True
    follow: bool = False
    bars: tuple[float, float] = (0.0, 0.0)  # cinema black bars at the top and bottom of the source, in pixels

    def picture(self) -> Box:
        """The source without its black bars: the gameplay is taken from here, so no bar reaches the Short."""
        top, bottom = self.bars
        return Box(0, top, self.src_w, max(self.src_h - top - bottom, 1))

    def point(self, t: float) -> tuple[float, float]:
        """Where the camera points at clip time t. Across a scene cut it jumps, never glides."""
        ts, xs, ys = self.path["t"], self.path["x"], self.path["y"]
        i = int(np.searchsorted(ts, t, side="right"))
        if 0 < i < len(ts):
            for c in self.path.get("cuts") or []:
                if ts[i - 1] < c <= ts[i]:
                    j = i if t >= c else i - 1
                    return float(xs[j]), float(ys[j])
        return float(np.interp(t, ts, xs)), float(np.interp(t, ts, ys))

    def zoom_factor(self, t: float) -> float:
        return zoom_at(t, self.anchor) if self.zoom else 1.0

    def _vertical_crop(self, t: float, base: float = 1.0) -> Box:
        W, H = self.src_w, self.src_h
        z = base * self.zoom_factor(t)
        pic = self.picture()
        h = pic.h / z
        w = h * 9 / 16
        if w > W:  # a source already narrower than 9:16
            w = W / z
            h = min(H, w * 16 / 9)
        x, y = self.point(t)
        crop = Box(x - w / 2, y - h / 2, w, h).inside(W, H)
        if crop.y < pic.y or crop.y1 > pic.y1:  # keep the crop off the black bars
            crop = Box(crop.x, min(max(crop.y, pic.y), pic.y1 - crop.h), crop.w, crop.h)
        return crop

    def regions(self, t: float) -> list[tuple[Box, Box, str]]:
        """(source box, output box in 1080x1920, role) for the frame at clip time t, drawn in order.

        Role: "main" (the picture), "cam" (the streamer's facecam) or "bg" (a dimmed, blurred backdrop).
        """
        W, H = self.src_w, self.src_h
        cam = self.facecam
        full = Box(0, 0, OUT_W, OUT_H)
        if self.layout == "split" and cam is not None:
            top = Box(0, 0, OUT_W, round(OUT_H * SPLIT_TOP))
            bottom = Box(0, top.h, OUT_W, OUT_H - top.h)
            # fill the panel from inside the facecam, so no gameplay shows around the streamer
            cam_src = cam.within_aspect(top.w / top.h).zoomed(self.zoom_factor(t)).inside(W, H)
            game_src = self.picture().within_aspect(bottom.w / bottom.h)
            # slide the gameplay away from the facecam (it is already on screen above), but not far
            if game_src.overlap(cam) > 0:
                margin = 0.01 * W  # clear of the overlay's border too
                if cam.cx > game_src.cx:
                    shift = -min(game_src.x1 - cam.x + margin, MAX_GAME_SHIFT * W)
                else:
                    shift = min(cam.x1 - game_src.x + margin, MAX_GAME_SHIFT * W)
                game_src = Box(game_src.x + shift, game_src.y, game_src.w, game_src.h).inside(W, H)
            return [(cam_src, top, "cam"), (game_src, bottom, "main")]
        if self.layout == "facecam_inset" and cam is not None:
            background = self.picture().within_aspect(OUT_W / OUT_H)
            inset = Box(OUT_W - INSET_MARGIN - INSET_W, INSET_TOP, INSET_W, INSET_W * cam.h / cam.w)
            return [(background, full, "main"), (cam, inset, "cam")]
        if self.layout == "reaction" and cam is not None:
            fg_h = OUT_W * cam.h / cam.w
            fg = Box(0, OUT_H * REACTION_CENTRE - fg_h / 2, OUT_W, fg_h)
            return [(cam.within_aspect(OUT_W / OUT_H), full, "bg"),
                    (cam.zoomed(self.zoom_factor(t)).inside(W, H), fg, "cam")]
        base = TIGHT_ZOOM if self.layout == "reaction" else 1.0
        return [(self._vertical_crop(t, base), full, "main")]

    def faces_out(self, t: float, faces: list[Box]) -> list[Box]:
        """Where the given source faces appear in the Short at time t.

        Faces on the blurred backdrop do not count, and a face hidden under a
        region drawn later (the facecam inset) is not visible either.
        """
        regions = self.regions(t)
        seen: list[tuple[int, Box]] = []
        for n, (src, dst, role) in enumerate(regions):
            if role == "bg":
                continue
            sx, sy = dst.w / src.w, dst.h / src.h
            for f in faces:
                if src.overlap(f) <= 0:  # a face half out of the crop still shows its other half
                    continue
                box = Box(dst.x + (f.x - src.x) * sx, dst.y + (f.y - src.y) * sy, f.w * sx, f.h * sy)
                x0, y0 = max(box.x, dst.x), max(box.y, dst.y)
                x1, y1 = min(box.x1, dst.x1), min(box.y1, dst.y1)
                if x1 > x0 and y1 > y0:
                    seen.append((n, Box(x0, y0, x1 - x0, y1 - y0)))
        return [b for n, b in seen
                if not any(dst.contains(b.cx, b.cy) for m, (_, dst, _r) in enumerate(regions) if m > n)]

    def to_dict(self) -> dict[str, Any]:
        return {"layout": self.layout, "src": [self.src_w, self.src_h], "path": self.path,
                "facecam": self.facecam.to_list() if self.facecam else None,
                "anchor": self.anchor, "zoom": self.zoom, "follow": self.follow, "bars": list(self.bars)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "FramePlan":
        return FramePlan(layout=d["layout"], src_w=int(d["src"][0]), src_h=int(d["src"][1]), path=d["path"],
                         facecam=Box.of(d["facecam"]) if d.get("facecam") else None, anchor=d.get("anchor"),
                         zoom=bool(d.get("zoom", True)), follow=bool(d.get("follow", False)),
                         bars=tuple(float(v) for v in (d.get("bars") or (0.0, 0.0))))


def plan_clip(
    samples: list[Sample], src_w: int, src_h: int, requested: str | None, anchor: float | None,
    cuts: list[float], zoom: bool = True, facecam: Box | None = None, video_checked: bool = False,
    bars: tuple[float, float] = (0.0, 0.0),
) -> tuple[FramePlan, Subject, str]:
    """The framing for one clip from its face samples: layout, camera path and zoom."""
    subject = classify(samples, src_w, src_h, facecam, video_checked)
    layout, reason = choose_layout(subject, requested)
    follow = layout in ("full", "reaction") and subject.kind == "face"
    path = camera_path(samples, src_w, src_h, follow, cuts)
    return FramePlan(layout, src_w, src_h, path, subject.facecam, anchor, zoom, follow, bars), subject, reason


# ---- composing a frame ------------------------------------------------------------------------


def compose(frame: np.ndarray, regions: list[tuple[Box, Box, str]], out_w: int, out_h: int,
            src_scale: float = 1.0) -> np.ndarray:
    """Build one output frame from the (possibly scaled-down) source frame and the layout's regions."""
    import cv2

    k = out_w / OUT_W
    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    H, W = frame.shape[:2]
    for src_box, dst, role in regions:
        src = src_box.scaled(src_scale)
        x0, y0 = int(round(max(src.x, 0))), int(round(max(src.y, 0)))
        x1, y1 = int(round(min(src.x1, W))), int(round(min(src.y1, H)))
        dx0, dy0 = max(int(round(dst.x * k)), 0), max(int(round(dst.y * k)), 0)
        dx1, dy1 = min(int(round(dst.x1 * k)), out_w), min(int(round(dst.y1 * k)), out_h)
        if x1 - x0 < 2 or y1 - y0 < 2 or dx1 - dx0 < 2 or dy1 - dy0 < 2:
            continue
        piece = frame[y0:y1, x0:x1]
        size = (dx1 - dx0, dy1 - dy0)
        if role == "bg":
            small = cv2.resize(piece, (max(2, size[0] // 16), max(2, size[1] // 16)), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 2.0)
            canvas[dy0:dy1, dx0:dx1] = (cv2.resize(small, size, interpolation=cv2.INTER_LINEAR) * 0.5).astype(np.uint8)
            continue
        shrink = size[0] < piece.shape[1]
        out = cv2.resize(piece, size, interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LANCZOS4)
        if role == "cam" and size[0] > CAM_SHARPEN_FROM * piece.shape[1]:
            # a small webcam blown up to fill a panel looks soft: a light unsharp mask brings back its detail
            soft = cv2.GaussianBlur(out, (0, 0), 1.0 + 0.4 * size[0] / piece.shape[1])
            out = cv2.addWeighted(out, 1.0 + CAM_SHARPEN, soft, -CAM_SHARPEN, 0)
        canvas[dy0:dy1, dx0:dx1] = out
        if role == "cam" and len(regions) > 1:  # a thin white edge where the streamer's picture meets the rest
            edge = max(1, round(4 * k))
            if dy0 == 0 and dx0 == 0 and dx1 == out_w:  # split screen: a line under the top panel
                canvas[max(dy1 - edge, 0):dy1, :] = 255
            else:
                cv2.rectangle(canvas, (dx0, dy0), (dx1 - 1, dy1 - 1), (255, 255, 255), edge)
    return canvas


def decode_size(src_w: int, src_h: int, out_w: int) -> tuple[tuple[int, int], float]:
    """How large to decode the source for an output of this width, and the scale from source pixels."""
    cap = 1920 if out_w >= OUT_W else 960
    w = _even(min(src_w, cap))
    h = _even(w * src_h / src_w)
    return (w, h), w / src_w


def drawer(plan: FramePlan, out_w: int, src_scale: float) -> Callable[[np.ndarray, float], np.ndarray]:
    out_h = out_w * 16 // 9
    return lambda frame, t: compose(frame, plan.regions(t), out_w, out_h, src_scale)


# ---- keeping text off faces --------------------------------------------------------------------


def caption_band(position: float, size: float, outline: float = 0.0) -> Box:
    """The strip a one-line caption at this height covers, with its outline (lines stay in the centre width)."""
    half = size * 0.7 + outline
    y = OUT_H * position / 100
    return Box(CAPTION_SIDE, y - half, OUT_W - 2 * CAPTION_SIDE, 2 * half)


def covered_share(band: Box, faces_by_sample: list[list[Box]], allowed: float = FACE_OVERLAP) -> float:
    """Share of samples in which the box covers more than `allowed` of some face."""
    if not faces_by_sample:
        return 0.0
    hit = sum(1 for faces in faces_by_sample if any(band.overlap(f) > allowed * f.area for f in faces))
    return hit / len(faces_by_sample)


def choose_caption_position(
    faces_by_sample: list[list[Box]], position: int, size: int, outline: int
) -> tuple[int, float, str | None]:
    """The caption height to use: the style's own unless that covers a face, then the first that does not."""
    tried = []
    for p in [position, *[c for c in CAPTION_CANDIDATES if c != position]]:
        share = covered_share(caption_band(p, size, outline), faces_by_sample)
        tried.append((share, p))
        if share <= COVER_LIMIT:
            if p == position:
                return p, share, None
            return p, share, f"Captions moved from {position}% to {p}% down the frame so they do not cover a face."
    share, p = min(tried)
    return p, share, (f"Every caption height crosses a face at times; {p}% down crosses one least "
                      f"({share:.0%} of the clip).")


def choose_label_position(
    faces_by_sample: list[list[Box]], caption: Box, text_w: float, text_h: float
) -> tuple[float, float, str, float]:
    """Where the PART label goes during the opening seconds: clear of faces and of the captions."""
    y_top = OUT_H * 0.085
    candidates = [
        ("top", OUT_W / 2, y_top),
        ("top left", 60 + text_w / 2, y_top),
        ("top right", OUT_W - 60 - text_w / 2, y_top),
        ("lower top", OUT_W / 2, OUT_H * 0.16),
        ("middle", OUT_W / 2, OUT_H * 0.5),
    ]
    best = None
    for name, x, y in candidates:
        box = Box(x - text_w / 2, y - text_h / 2, text_w, text_h)
        if box.overlap(caption) > 0:
            continue
        share = covered_share(box, faces_by_sample, allowed=0.0)  # a label is solid: it may not touch a face
        if share == 0:
            return x, y, name, 0.0
        if best is None or share < best[3]:
            best = (x, y, name, share)
    return best if best else (OUT_W / 2, y_top, "top", 1.0)


def faces_in_output(plan: FramePlan, samples: list[Sample], until: float | None = None) -> list[list[Box]]:
    """Faces as they appear in the Short at each sample, with a safety margin for placing text.

    Each face box is grown by a tenth on every side, and a face counts as there
    one sample before and after it was seen: the detector misses a face now and
    then, and a face moves between samples.
    """
    seen = [[_grow(f, TEXT_MARGIN) for f in plan.faces_out(s.t, s.faces)] for s in samples]
    out = []
    for i, s in enumerate(samples):
        if until is not None and s.t > until:
            break
        out.append([*seen[i], *(seen[i - 1] if i > 0 else []), *(seen[i + 1] if i + 1 < len(seen) else [])])
    return out


def _grow(b: Box, share: float) -> Box:
    return Box(b.x - share * b.w, b.y - share * b.h, b.w * (1 + 2 * share), b.h * (1 + 2 * share))


def choose_tag_position(
    faces_by_sample: list[list[Box]], keep_clear: list[Box], text_w: float, text_h: float
) -> tuple[float, float, str, float]:
    """Where the creator credit sits for the whole clip: a corner clear of faces, captions and the label.

    Always on the left: YouTube's search and menu icons sit in the top-right corner, its like, comment and
    share buttons down the right-hand side, and its channel name and title along the bottom.
    """
    candidates = [
        ("top left", 48 + text_w / 2, OUT_H * 0.045),
        ("left, above the title", 48 + text_w / 2, OUT_H * 0.74),
        ("left, middle", 48 + text_w / 2, OUT_H * 0.47),
    ]
    best = None
    for name, x, y in candidates:
        box = Box(x - text_w / 2, y - text_h / 2, text_w, text_h)
        if any(box.overlap(k) > 0 for k in keep_clear):
            continue
        share = covered_share(box, faces_by_sample, allowed=0.0)
        if share == 0:
            return x, y, name, 0.0
        if best is None or share < best[3]:
            best = (x, y, name, share)
    return best if best else (48 + text_w / 2, OUT_H * 0.045, "top left", 1.0)


def place_text(
    plan: FramePlan, samples: list[Sample], position: int, size: int, outline: int,
    label: tuple[str, float, float] | None, tag: tuple[str, float, float] | None = None,
) -> dict[str, Any]:
    """Caption height, PART label spot and creator credit spot for one clip, chosen so none covers a face.

    `label` and `tag` are (text, width, height) in output pixels, or None for none.
    """
    faces = faces_in_output(plan, samples)
    pos, share, note = choose_caption_position(faces, position, size, outline)
    band = caption_band(pos, size, outline)
    out: dict[str, Any] = {"caption_position": pos, "caption_face_share": round(share, 3), "caption_note": note,
                           "label": None, "tag": None}
    clear = [band]
    if label:
        text, w, h = label
        early = faces_in_output(plan, samples, LABEL_SECONDS)
        x, y, where, lshare = choose_label_position(early, band, w, h)
        out["label"] = {"text": text, "x": round(x), "y": round(y), "where": where, "until": LABEL_SECONDS,
                        "face_share": round(lshare, 3)}
        clear.append(Box(x - w / 2, y - h / 2, w, h))
    if tag:
        text, w, h = tag
        x, y, where, tshare = choose_tag_position(faces, clear, w, h)
        out["tag"] = {"text": text, "x": round(x), "y": round(y), "where": where, "face_share": round(tshare, 3)}
    return out
