"""Phase 8: framing (faces, facecam, layouts, camera path, zoom), text placement and the final render."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipforge import framing as fr
from clipforge import pipelines
from clipforge.app import create_app
from clipforge.captions import CaptionStyle, build_captions, label_size
from clipforge.framing import Box, FramePlan, Sample
from clipforge.media import measure_loudness, probe_media, render_framed, replace_audio

from .conftest import wait_for

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
W, H = 1920, 1080


def face(cx: float, cy: float, size: float) -> Box:
    return Box(cx - size / 2, cy - size * 0.6, size, size * 1.2)


def facecam_samples(n: int = 100, share: float = 0.8, seed: int = 1) -> list[Sample]:
    """A streamer in a bottom-right facecam most of the time, and now and then a big face in the game."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        faces = []
        if rng.random() < share:
            faces.append(face(1600 + rng.normal(0, 12), 850 + rng.normal(0, 8), 90))
        if rng.random() < 0.15:
            faces.append(face(rng.uniform(500, 1400), rng.uniform(300, 700), 300))
        out.append(Sample(i / 5, faces))
    return out


def speaker_samples(n: int = 100) -> list[Sample]:
    """A large face drifting from left of centre to right of centre, with detector noise."""
    rng = np.random.default_rng(2)
    return [Sample(i / 5, [face(700 + 6 * i + rng.normal(0, 15), 450 + rng.normal(0, 10), 320)]) for i in range(n)]


# ---- what kind of picture --------------------------------------------------------------


def test_a_facecam_is_found_where_the_streamer_sits_and_ignores_faces_in_the_game():
    subject = fr.classify(facecam_samples(), W, H)
    assert subject.kind == "facecam" and "bottom right" in subject.note
    cam = subject.facecam
    assert cam.contains(1600, 850) and cam.x > W / 2 and cam.y > H / 2, "the box is around the streamer"
    assert cam.w < 0.3 * W, "and does not reach into the middle of the game"


def test_a_speaker_is_followed_and_an_empty_picture_keeps_the_centre():
    assert fr.classify(speaker_samples(), W, H).kind == "face"
    assert fr.classify([Sample(i / 5, []) for i in range(50)], W, H).kind == "none"
    tiny = [Sample(i / 5, [face(960, 540, 30)] if i % 9 == 0 else []) for i in range(90)]
    assert fr.classify(tiny, W, H).kind == "none", "rare, tiny faces are not worth following"


def test_a_short_clip_dominated_by_a_face_in_the_game_still_uses_the_videos_facecam():
    pooled = facecam_samples(400)
    found = fr.find_facecam(pooled, W, H)
    clip = [Sample(i / 5, [face(900, 500, 320)] + ([face(1600, 850, 90)] if i % 6 == 0 else [])) for i in range(60)]
    assert fr.classify(clip, W, H).kind == "face", "on its own the clip looks like a speaker"
    subject = fr.classify(clip, W, H, found.facecam)
    assert subject.kind == "facecam" and fr.choose_layout(subject, "auto")[0] == "split"


def test_layout_choice_and_requests_that_cannot_be_met():
    cam = fr.Subject("facecam", Box(1400, 700, 400, 300), 0.8)
    speaker = fr.Subject("face", None, 0.9)
    nothing = fr.Subject("none")
    assert fr.choose_layout(cam, "auto")[0] == "split"
    assert fr.choose_layout(speaker, "auto")[0] == "full"
    assert fr.choose_layout(cam, "facecam_inset")[0] == "facecam_inset"
    layout, why = fr.choose_layout(speaker, "split")
    assert layout == "full" and "needs a facecam" in why
    assert fr.choose_layout(nothing, "reaction")[0] == "full"
    assert fr.choose_layout(speaker, "reaction")[0] == "reaction"


# ---- the camera path ---------------------------------------------------------------------


def test_the_camera_follows_smoothly_and_never_faster_than_its_limit():
    samples = speaker_samples()
    path = fr.camera_path(samples, W, H, follow=True)
    xs = np.array(path["x"])
    max_step = fr.MAX_PAN * (H * 9 / 16) / fr.SAMPLE_FPS
    assert np.abs(np.diff(xs)).max() <= max_step + 1e-6
    raw = np.array([s.faces[0].cx for s in samples])
    assert np.abs(np.diff(xs)).std() < np.abs(np.diff(raw)).std() / 3, "detector jitter is smoothed away"
    assert abs(xs[-1] - raw[-10:].mean()) < 60, "and it still ends up on the face"


def test_the_camera_jumps_at_a_scene_cut_instead_of_gliding():
    samples = [Sample(i / 5, [face(500 if i < 50 else 1400, 540, 300)]) for i in range(100)]
    plan = FramePlan("full", W, H, fr.camera_path(samples, W, H, True, cuts=[10.0]), follow=True, zoom=False)
    assert plan.point(9.9)[0] == pytest.approx(500, abs=5)
    assert plan.point(10.0)[0] == pytest.approx(1400, abs=5), "the new shot is framed from its first frame"
    xs = [plan.point(t)[0] for t in np.arange(9.0, 11.0, 1 / 30)]
    assert not any(600 < x < 1300 for x in xs), "no frame is caught halfway between the two"


def test_a_cut_the_scene_detector_missed_is_followed_at_once():
    # found in the gate: a shot change with no detected scene cut made the camera pan across for a second
    samples = [Sample(i / 5, [face(1300 if i < 30 else 600, 540, 400)]) for i in range(60)]
    plan = FramePlan("full", W, H, fr.camera_path(samples, W, H, True, cuts=[]), follow=True, zoom=False)
    assert plan.point(5.6)[0] == pytest.approx(1300, abs=5) and plan.point(6.0)[0] == pytest.approx(600, abs=5)
    assert all(plan.faces_out(s.t, s.faces) for s in samples), "the face is in the Short at every sample"


def test_two_people_are_framed_together_when_they_fit_and_the_camera_stays_with_one_when_not():
    both = [Sample(i / 5, [face(800, 500, 200), face(1100, 520, 190)]) for i in range(30)]
    xs = fr.camera_path(both, W, H, True)["x"]
    assert all(abs(x - 950) < 15 for x in xs), "centred between two people who fit in the frame"
    rng = np.random.default_rng(3)
    apart = [Sample(i / 5, [face(400, 500, 200 + rng.normal(0, 10)), face(1500, 500, 200 + rng.normal(0, 10))])
             for i in range(40)]
    xs = np.array(fr.camera_path(apart, W, H, True)["x"])
    assert np.all(np.abs(xs - xs[0]) < 30), "no flipping between two people of the same size"


def test_tiny_faces_in_the_background_are_not_followed():
    samples = [Sample(i / 5, [face(900, 500, 400)] if i < 20 else [face(1700, 300, 30)]) for i in range(40)]
    xs = fr.camera_path(samples, W, H, True)["x"]
    assert all(abs(x - 900) < 5 for x in xs)


def test_a_lost_face_is_bridged_rather_than_snapping_to_the_centre():
    samples = [Sample(i / 5, [face(600, 540, 300)] if not 20 <= i < 30 else []) for i in range(60)]
    xs = fr.camera_path(samples, W, H, True)["x"]
    assert all(abs(x - 600) < 5 for x in xs)


def test_the_zoom_pushes_in_on_the_key_moment_and_eases_back():
    assert fr.zoom_at(3.0, None) == 1.0
    assert fr.zoom_at(4.0, 5.0) == 1.0
    assert fr.zoom_at(5.6, 5.0) == pytest.approx(fr.ZOOM_MAX)
    assert 1.0 < fr.zoom_at(5.0, 5.0) < fr.ZOOM_MAX
    assert fr.zoom_at(10.0, 5.0) == 1.0
    steps = np.diff([fr.zoom_at(t, 5.0) for t in np.arange(4.0, 9.0, 1 / 30)])
    assert np.abs(steps).max() < 0.012, "no visible jump from one frame to the next"


# ---- layouts --------------------------------------------------------------------------------


def aspect(b: Box) -> float:
    return b.w / b.h


@pytest.mark.parametrize("layout", fr.LAYOUTS)
def test_every_layout_fills_the_frame_without_stretching_and_stays_inside_the_source(layout):
    cam = Box(1500, 760, 380, 280)
    plan = FramePlan(layout, W, H, {"t": [0.0], "x": [1700.0], "y": [200.0], "cuts": []}, cam, anchor=1.0)
    for t in (0.0, 1.2, 5.0):
        regions = plan.regions(t)
        for src, dst, role in regions:
            assert src.x >= -0.01 and src.y >= -0.01 and src.x1 <= W + 0.01 and src.y1 <= H + 0.01
            assert aspect(src) == pytest.approx(aspect(dst), rel=0.02) or role == "cam" and layout == "facecam_inset", \
                f"{layout} {role}: picture would be stretched"
        covered = sum(dst.area for _, dst, role in regions if role in ("main", "bg") or layout == "split")
        assert covered >= fr.OUT_W * fr.OUT_H - 1


def test_split_screen_keeps_gameplay_out_of_the_streamer_panel_and_the_facecam_out_of_the_game():
    cam = Box(1300, 760, 560, 300)
    plan = FramePlan("split", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, cam)
    (cam_src, top, role_a), (game_src, bottom, role_b) = plan.regions(0.0)
    assert (role_a, role_b) == ("cam", "main") and top.h == round(fr.OUT_H * fr.SPLIT_TOP)
    assert cam.x <= cam_src.x and cam_src.x1 <= cam.x1 and cam.y <= cam_src.y and cam_src.y1 <= cam.y1
    assert game_src.overlap(cam) == 0, "the facecam is not shown twice"
    assert abs(game_src.cx - W / 2) <= fr.MAX_GAME_SHIFT * W + 1, "the game stays roughly centred"


def test_faces_are_carried_into_the_short_and_hidden_ones_are_dropped():
    cam = Box(1500, 760, 380, 280)
    split = FramePlan("split", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, cam, zoom=False)
    [f] = split.faces_out(0.0, [face(1690, 900, 90)])
    assert f.y1 <= fr.OUT_H * fr.SPLIT_TOP + 1, "the streamer's face is in the top panel"
    inset = FramePlan("facecam_inset", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, cam, zoom=False)
    shown = inset.faces_out(0.0, [face(1690, 900, 90)])
    assert len(shown) == 1 and shown[0].x > fr.OUT_W / 2, "only the inset copy counts"
    full = FramePlan("full", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    assert full.faces_out(0.0, [face(100, 540, 200)]) == [], "a face outside the 9:16 crop is not in the Short"


# ---- keeping text off faces ----------------------------------------------------------------


def test_captions_stay_put_when_no_face_is_in_the_way():
    plan = FramePlan("full", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    place = fr.place_text(plan, [Sample(i / 5, [face(960, 300, 200)]) for i in range(20)], 76, 88, 7, None)
    assert place["caption_position"] == 76 and place["caption_note"] is None


def test_captions_move_off_a_face_and_say_so():
    plan = FramePlan("full", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    low_face = [Sample(i / 5, [face(960, 820, 240)]) for i in range(20)]  # at about 76% down the Short
    place = fr.place_text(plan, low_face, 76, 88, 7, None)
    assert place["caption_position"] != 76 and "do not cover a face" in place["caption_note"]
    faces = fr.faces_in_output(plan, low_face)
    assert fr.covered_share(fr.caption_band(place["caption_position"], 88, 7), faces) == 0


def test_the_part_label_avoids_faces_and_the_captions():
    plan = FramePlan("full", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    top_face = [Sample(i / 5, [face(960, 120, 200)]) for i in range(20)]  # a face where the label would go
    w, h = label_size("PART 1/2")
    place = fr.place_text(plan, top_face, 76, 88, 7, ("PART 1/2", w, h))
    label = place["label"]
    box = Box(label["x"] - w / 2, label["y"] - h / 2, w, h)
    assert label["where"] != "top" and label["face_share"] == 0
    assert box.overlap(fr.caption_band(place["caption_position"], 88, 7)) == 0


def test_text_keeps_a_margin_round_faces_and_through_a_missed_detection():
    plan = FramePlan("full", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    # a face seen in every other sample only: the gaps still count as "there"
    flicker = [Sample(i / 5, [face(960, 820, 240)] if i % 2 == 0 else []) for i in range(20)]
    faces = fr.faces_in_output(plan, flicker)
    assert all(faces), "no sample is treated as face-free"
    raw = plan.faces_out(0.0, flicker[0].faces)[0]
    grown = faces[0][0]
    assert grown.x < raw.x and grown.x1 > raw.x1 and grown.w == pytest.approx(raw.w * 1.2)


def test_the_part_label_is_drawn_for_the_opening_seconds_only():
    words = [{"s": 0.5, "e": 0.9, "w": "hello", "p": 0.9}]
    label = {"text": "PART 2/3", "x": 540, "y": 163, "until": 2.5}
    result = build_captions(words, [[0.0, 3.0]], 3.0, CaptionStyle(), position=54, label=label)
    assert "Style: Label," in result.ass
    assert "Dialogue: 1,0:00:00.00,0:00:02.50,Label,,0,0,0,,{\\pos(540,163)" in result.ass and "PART 2/3" in result.ass
    assert "\\pos(540,1037)" in result.ass, "the caption is at the height framing chose (54% of 1920)"
    off = build_captions(words, [[0.0, 3.0]], 3.0, CaptionStyle(enabled=False), label=label)
    assert "PART 2/3" in off.ass and "HELLO" not in off.ass, "the label shows even with captions off"


# ---- finding the facecam's edges -------------------------------------------------------------


def test_the_facecam_box_snaps_to_the_overlays_real_edges():
    rng = np.random.default_rng(5)

    def blocks(h: int, w: int) -> np.ndarray:
        """Random 8-pixel blocks, shifted a random amount each time, so no block edge stays in one place."""
        big = np.repeat(np.repeat(rng.integers(0, 255, (h // 8 + 2, w // 8 + 2, 3), dtype=np.uint8), 8, 0), 8, 1)
        dy, dx = rng.integers(0, 8, 2)
        return big[dy:dy + h, dx:dx + w]

    frames = []
    for _ in range(20):
        img = blocks(360, 640)  # the game
        img[250:346, 420:572] = blocks(96, 152)  # the facecam
        frames.append(img)
    guess = Box(1300, 720, 420, 330)  # in 1920x1080 source pixels; the overlay is at 1260,750 to 1716,1038
    box, sides = fr.snap_to_overlay(frames, guess, W, H)
    assert set(sides) == {"left", "right", "top", "bottom"}
    assert box.x == pytest.approx(1260, abs=12) and box.x1 == pytest.approx(1716, abs=12)
    assert box.y == pytest.approx(750, abs=12) and box.y1 == pytest.approx(1038, abs=12)


def test_an_edge_far_from_the_estimate_is_found_between_the_faces_and_the_estimate():
    # found in the gate: faces in a widescreen facecam put the estimate's top 300 px above the overlay
    rng = np.random.default_rng(7)
    frames = []
    for _ in range(20):
        img = cv2_blur(rng.integers(0, 255, (360, 640, 3), dtype=np.uint8))
        img[255:346, 412:626] = cv2_blur(rng.integers(0, 255, (91, 214, 3), dtype=np.uint8))  # 1236,765 - 1878,1038
        frames.append(img)
    guess = Box(1240, 465, 632, 566)  # top far too high
    core = Box(1400, 800, 300, 200)  # where the faces were
    box, sides = fr.snap_to_overlay(frames, guess, W, H, core)
    assert "top" in sides and box.y == pytest.approx(765, abs=12)
    assert box.x == pytest.approx(1236, abs=12) and box.x1 == pytest.approx(1878, abs=12)


def cv2_blur(img: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.GaussianBlur(img, (0, 0), 3)


def test_once_the_whole_video_has_no_facecam_a_clip_does_not_find_one_on_its_own():
    clip = facecam_samples(60)  # looks like a facecam taken alone (someone off to one side)
    assert fr.classify(clip, W, H).kind == "facecam"
    assert fr.classify(clip, W, H, video_checked=True).kind != "facecam"


def test_without_a_clear_edge_the_estimate_is_kept():
    rng = np.random.default_rng(6)
    frames = [rng.integers(0, 255, (360, 640, 3), dtype=np.uint8) for _ in range(10)]
    guess = Box(1300, 720, 420, 330)
    assert fr.snap_to_overlay(frames, guess, W, H) == (guess, [])


# ---- composing and rendering ------------------------------------------------------------------


def test_a_composed_split_frame_has_the_streamer_on_top_and_a_line_between():
    frame = np.zeros((540, 960, 3), np.uint8)
    frame[:, :] = (0, 0, 200)  # the game is red
    frame[380:520, 700:940] = (200, 0, 0)  # the facecam is blue
    cam = Box(1400, 760, 480, 280)
    plan = FramePlan("split", W, H, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, cam, zoom=False)
    out = fr.compose(frame, plan.regions(0.0), 540, 960, src_scale=0.5)
    assert out.shape == (960, 540, 3)
    assert out[100, 270].tolist() == [200, 0, 0] and out[700, 270].tolist() == [0, 0, 200]
    assert out[round(960 * fr.SPLIT_TOP) - 1, 270].tolist() == [255, 255, 255]


def tone_video(settings, path: Path, levels: list[tuple[float, float, float]], seconds: float,
               size: str = "640x360") -> Path:
    """A test picture with a 300 Hz tone at the given (start, end, amplitude) levels."""
    sr = 48000
    audio = np.zeros(int(seconds * sr), np.float32)
    for a, b, amp in levels:
        i, j = int(a * sr), int(b * sr)
        audio[i:j] = amp * np.sin(2 * np.pi * 300 * np.arange(j - i) / sr)
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=s={size}:r=30:d={seconds}", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    return path


def decode(settings, path: Path) -> np.ndarray:
    raw = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-af", "pan=mono|c0=c0",
         "-ar", "48000", "-f", "f32le", "-"],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)


def test_a_framed_preview_is_9_16_with_the_clips_sound(settings, tmp_path):
    video = tone_video(settings, tmp_path / "v.mp4", [(0, 1, 0.2), (1, 2, 0.2)], 2.0)
    plan = fr.FramePlan("full", 640, 360, {"t": [0.0], "x": [320.0], "y": [180.0], "cuts": []}, zoom=False)
    size, scale = fr.decode_size(640, 360, 540)
    out = render_framed(settings, video, tmp_path / "p.mp4", [(0.0, 0.8), (1.2, 2.0)], fr.drawer(plan, 540, scale),
                        size, None, width=540, fps=30)
    info = probe_media(settings, out)
    assert (info["width"], info["height"]) == (540, 960) and info["has_audio"]
    assert info["duration"] == pytest.approx(1.6, abs=0.06), "two ranges joined, nothing more"


def test_the_final_render_is_1080x1920_at_the_target_loudness_with_one_gain_for_everything(settings, tmp_path):
    # quiet first half, 12 dB louder second half: levelling must lift both by the same amount
    video = tone_video(settings, tmp_path / "v.mp4", [(0, 2, 0.03), (2, 4, 0.12)], 4.0)
    before = measure_loudness(settings, video, [(0.0, 4.0)], audio_channels=1)
    gain = pipelines.TARGET_LUFS - before["lufs"]
    plan = fr.FramePlan("full", 640, 360, {"t": [0.0], "x": [320.0], "y": [180.0], "cuts": []}, zoom=False)
    size, scale = fr.decode_size(640, 360, fr.OUT_W)
    out = render_framed(settings, video, tmp_path / "final.mp4", [(0.0, 4.0)], fr.drawer(plan, fr.OUT_W, scale),
                        size, None, width=fr.OUT_W, fps=30, final=True, gain_db=gain, audio_channels=1,
                        peak_limit=pipelines.PEAK_CEILING)
    info = probe_media(settings, out)
    assert (info["width"], info["height"], info["video_codec"], info["audio_codec"]) == (1080, 1920, "h264", "aac")
    after = measure_loudness(settings, out, [], whole_file=True)
    assert after["lufs"] == pytest.approx(pipelines.TARGET_LUFS, abs=1.0)
    assert after["true_peak"] <= -0.5
    src, dst = decode(settings, video), decode(settings, out)
    sr = 48000
    quiet = rms_db(dst[int(0.5 * sr):int(1.5 * sr)]) - rms_db(src[int(0.5 * sr):int(1.5 * sr)])
    loud = rms_db(dst[int(2.5 * sr):int(3.5 * sr)]) - rms_db(src[int(2.5 * sr):int(3.5 * sr)])
    assert quiet == pytest.approx(gain, abs=0.5) and loud == pytest.approx(gain, abs=0.5), \
        "a single gain: the difference between quiet and loud is kept"


def test_the_sound_can_be_encoded_again_with_a_lower_ceiling_without_touching_the_picture(settings, tmp_path):
    video = tone_video(settings, tmp_path / "v.mp4", [(0, 2, 0.9)], 2.0)
    plan = fr.FramePlan("full", 640, 360, {"t": [0.0], "x": [320.0], "y": [180.0], "cuts": []}, zoom=False)
    size, scale = fr.decode_size(640, 360, 540)
    out = render_framed(settings, video, tmp_path / "f.mp4", [(0.0, 2.0)], fr.drawer(plan, 540, scale), size, None,
                        width=540, fps=30, final=True, gain_db=0.0, peak_limit=1.0, audio_channels=1)
    def video_packets(path):
        return subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "packet=size", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                              creationflags=NO_WINDOW).stdout
    picture = video_packets(out)
    loud = measure_loudness(settings, out, [], whole_file=True)["true_peak"]
    replace_audio(settings, out, video, [(0.0, 2.0)], audio_channels=1, gain_db=0.0, peak_limit=10 ** (-6 / 20))
    assert measure_loudness(settings, out, [], whole_file=True)["true_peak"] < loud - 3
    assert video_packets(out) == picture, "the picture is copied, not encoded again"


# ---- through the app ------------------------------------------------------------------------


class FakeDetector:
    """A streamer's face in the bottom-right corner of a 640x360 video, in every frame."""

    def detect(self, frame):
        h, w = frame.shape[:2]
        return [(w * 0.80, h * 0.70, w * 0.06, h * 0.12)]


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def wait_ready(client, project_id):
    def done():
        jobs = client.get(f"/api/jobs?project_id={project_id}").json()
        if not jobs or jobs[0]["status"] in ("queued", "running"):
            return None
        p = client.get(f"/api/projects/{project_id}").json()
        return p if p["status"] != "processing" else None

    return wait_for(done, timeout=180)


def make_project(client, video, **options):
    options = {"min_score": 0, "target_duration": 10, **options}
    p = client.post("/api/projects", json={"source": "local", "path": str(video), "rights_status": "own",
                                           "options": options}).json()
    return wait_ready(client, p["id"])


def only_clip(client, project):
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    return clip


def test_a_facecam_clip_is_framed_as_split_screen_and_says_why(client, sample_video, monkeypatch):
    monkeypatch.setattr(pipelines, "make_detector", lambda _s: FakeDetector())
    project = make_project(client, sample_video)
    clip = only_clip(client, project)
    framing = clip["framing"]
    assert framing["layout"] == "split" and framing["subject"]["kind"] == "facecam"
    assert "facecam" in framing["reason"] and framing["samples"] >= 5 and framing["detector"]
    assert project["analysis"]["framing"]["layouts"] == {"split": 1}
    assert project["analysis"]["framing"]["facecam"]["kind"] == "facecam"
    assert clip["has_vertical"]


def test_without_the_face_model_the_centre_is_kept_and_the_reason_given(client, sample_video, monkeypatch):
    monkeypatch.setattr(pipelines, "make_detector", lambda _s: None)
    clip = only_clip(client, make_project(client, sample_video))
    assert clip["framing"]["layout"] == "full" and not clip["framing"]["detector"]
    assert "not installed" in clip["framing"]["subject"]["note"]


def test_faces_are_found_once_and_reused_when_the_layout_changes(client, sample_video, monkeypatch, settings):
    calls = []

    class Counting(FakeDetector):
        def detect(self, frame):
            calls.append(1)
            return super().detect(frame)

    monkeypatch.setattr(pipelines, "make_detector", lambda _s: Counting())
    project = make_project(client, sample_video)
    scanned = len(calls)
    assert scanned > 0
    options = {**project["options"], "layout": "facecam_inset"}
    client.patch(f"/api/projects/{project['id']}/options", json=options)
    project = wait_ready(client, project["id"])
    jobs = client.get(f"/api/jobs?project_id={project['id']}").json()
    assert jobs[0]["kind"] == "reframe" and [s["name"] for s in jobs[0]["stages"]] == ["framing", "captions"]
    assert only_clip(client, project)["framing"]["layout"] == "facecam_inset"
    assert len(calls) == scanned, "the face scan was kept, not repeated"


def test_one_clips_layout_can_be_chosen_by_hand_and_set_back(client, sample_video, monkeypatch):
    monkeypatch.setattr(pipelines, "make_detector", lambda _s: FakeDetector())
    project = make_project(client, sample_video)
    clip = only_clip(client, project)
    r = client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}/framing", json={"layout": "reaction"})
    assert r.status_code == 200
    clip = only_clip(client, wait_ready(client, project["id"]))
    assert clip["framing"]["layout"] == "reaction" and clip["framing"]["requested"] == "reaction"
    client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}/framing", json={"layout": "auto"})
    clip = only_clip(client, wait_ready(client, project["id"]))
    assert clip["framing"]["layout"] == "split" and clip["framing"]["requested"] is None


def test_only_approved_clips_are_rendered_as_final_shorts(client, sample_video, settings, monkeypatch):
    monkeypatch.setattr(pipelines, "make_detector", lambda _s: FakeDetector())
    project = make_project(client, sample_video)
    clip = only_clip(client, project)
    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/render-final", json={}).status_code == 409
    assert client.post(f"/api/projects/{pid}/render-final", json={"clip_ids": [clip["id"]]}).status_code == 409
    assert client.get(f"/api/projects/{pid}/clips/{clip['id']}/final").status_code == 404

    client.patch(f"/api/projects/{pid}/clips/{clip['id']}", json={"review": "approved"})
    assert client.post(f"/api/projects/{pid}/render-final", json={}).status_code == 200
    project = wait_ready(client, pid)
    clip = only_clip(client, project)
    final = clip["final"]
    assert final["exists"] and final["passed"], final["checks"]
    assert (final["width"], final["height"]) == (1080, 1920) and final["layout"] == "split"
    assert "file" not in final, "the server's file path is not sent to the dashboard"
    assert final["loudness"]["lufs"] == pytest.approx(-14, abs=1.0)
    assert project["analysis"]["final"]["made"] == 1
    r = client.get(f"/api/projects/{pid}/clips/{clip['id']}/final?download=1")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert final["name"] in r.headers["content-disposition"]
    folder = settings.project_dir(pid) / "final"
    assert [p.name for p in folder.iterdir()] == [final["name"]], "no half-written or caption files left behind"


def test_changing_part_labels_recaptions_without_reframing(client, sample_video, monkeypatch):
    monkeypatch.setattr(pipelines, "make_detector", lambda _s: FakeDetector())
    project = make_project(client, sample_video)
    client.patch(f"/api/projects/{project['id']}/options", json={**project["options"], "part_labels": False})
    wait_ready(client, project["id"])
    jobs = client.get(f"/api/jobs?project_id={project['id']}").json()
    assert jobs[0]["kind"] == "caption"


def test_a_steady_line_on_the_streamer_is_not_taken_for_the_overlays_edge():
    # found on a real stream: a headset line inside the faces cropped the facecam to a blurry patch of face
    rng = np.random.default_rng(9)
    frames = []
    for _ in range(20):
        img = cv2_blur(rng.integers(0, 255, (360, 640, 3), dtype=np.uint8))
        img[0:92, 0:163] = cv2_blur(rng.integers(0, 255, (92, 163, 3), dtype=np.uint8))  # overlay 0,0 - 489,276
        img[44:46, 60:110] = 255  # a headset band across the face, the same in every frame
        frames.append(img)
    core = Box(200, 93, 124, 150)  # the faces
    guess = Box(132, 35, 260, 318)  # face-size estimate: too small on the right
    box, sides = fr.snap_to_overlay(frames, guess, W, H, core)
    assert "right" in sides and box.x1 == pytest.approx(489, abs=12)
    assert box.y1 == pytest.approx(276, abs=12) and box.y < core.y and box.x < core.x
