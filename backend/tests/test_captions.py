"""Phase 6: word-synced captions, styles, checks, burned-in vertical previews."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from clipforge import captions as cap
from clipforge.app import create_app
from clipforge.captions import CaptionStyle, build_captions, clip_words, highlighted_at
from clipforge.media import probe_media, render_vertical

from .conftest import wait_for

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def w(s: float, e: float, text: str, p: float = 0.95) -> dict:
    return {"s": s, "e": e, "w": text, "p": p}


SPEECH = [
    w(10.0, 10.3, "Why"), w(10.35, 10.5, "is"), w(10.55, 10.8, "that"), w(10.85, 11.3, "bridge"),
    w(11.35, 11.9, "opening?"),
    w(12.6, 12.8, "I"), w(12.85, 13.0, "am"), w(13.05, 13.2, "on"), w(13.25, 13.4, "the"), w(13.45, 13.9, "bridge!"),
]


# ---- words onto the clip's clock --------------------------------------------------


def test_words_are_moved_onto_the_clip_clock():
    out = clip_words(SPEECH, [[9.5, 14.5]])
    assert [x.text for x in out] == [x["w"] for x in SPEECH]
    assert out[0].s == pytest.approx(0.5) and out[-1].e == pytest.approx(4.4)


def test_removed_dead_air_shifts_later_words_earlier():
    # the clip keeps 9.5-11.95 and 12.5-14.5: the 0.55 s between them is cut out
    out = clip_words(SPEECH, [[9.5, 11.95], [12.5, 14.5]])
    assert out[4].text == "opening?" and out[4].e == pytest.approx(2.4)
    assert out[5].text == "I" and out[5].s == pytest.approx(2.45 + 0.1)
    assert [x.piece for x in out] == [0] * 5 + [1] * 5


def test_words_outside_the_clip_are_left_out():
    out = clip_words(SPEECH + [w(30.0, 30.5, "later.")], [[9.5, 14.5]])
    assert "later." not in [x.text for x in out]


# ---- lines ------------------------------------------------------------------------


def lines_for(words, segments=None, length=6.0, **style):
    return build_captions(words, segments or [[9.5, 15.5]], length, CaptionStyle(**style))


def test_lines_are_short_and_break_at_the_end_of_a_sentence():
    result = lines_for(SPEECH)
    texts = [ln["text"] for ln in result.lines]
    assert texts == ["WHY IS THAT", "BRIDGE OPENING?", "I AM ON", "THE BRIDGE!"]
    assert all(len(ln["words"]) <= 3 for ln in result.lines)


def test_a_pause_starts_a_new_line():
    words = [w(0.0, 0.3, "wait"), w(1.2, 1.5, "what")]
    assert [ln["text"] for ln in lines_for(words, [[0, 3]], 3.0).lines] == ["WAIT", "WHAT"]


def test_lines_never_span_removed_dead_air():
    words = [w(0.0, 0.3, "one"), w(0.35, 0.6, "two"), w(5.0, 5.3, "three")]
    # 0.6-5.0 is cut out, so "two" and "three" are 0.05 s apart on the clip, but still different lines
    result = lines_for(words, [[0.0, 0.62], [4.97, 5.5]], 1.15)
    assert [ln["text"] for ln in result.lines] == ["ONE TWO", "THREE"]


def test_long_words_are_split_so_each_line_fits_the_width():
    words = [w(i * 0.5, i * 0.5 + 0.4, "incomprehensibilities") for i in range(4)]
    result = lines_for(words, [[0, 3]], 3.0)
    assert all(len(ln["words"]) == 1 for ln in result.lines)
    assert next(c for c in result.checks if c.name == "fits_the_width").ok


def test_width_follows_the_renderers_sizing_rule():
    """Found by the frame-by-frame check: the renderer sizes text by the font's full line height."""
    viral = CaptionStyle().resolve()
    # measured on a rendered frame: 'I KNOW.' at size 88 is about 253 px wide without its outline
    assert cap.text_width("I KNOW.", viral) - 2 * viral.outline == pytest.approx(253, abs=15)


def test_line_width_comes_from_the_font_itself():
    viral = CaptionStyle().resolve()
    narrow = cap.text_width("IIII", viral)
    wide = cap.text_width("WWWW", viral)
    assert wide > 2 * narrow, "measured per glyph, not by counting characters"
    anton = CaptionStyle(preset="bold", size=88).resolve()
    assert cap.text_width("BRIDGE", anton) < cap.text_width("BRIDGE", viral), "Anton is a condensed face"
    # a common three-word line at the default size fits comfortably
    assert cap.text_width("WHY IS THAT", viral) < cap.FRAME_W - 2 * cap.SIDE_MARGIN


def test_minimal_style_keeps_case_and_puts_more_words_on_a_line():
    result = lines_for(SPEECH, preset="minimal")
    assert result.lines[0]["text"] == "Why is that bridge opening?"


# ---- timing checks ----------------------------------------------------------------


def test_all_timing_checks_pass_for_normal_speech():
    result = lines_for(SPEECH)
    failed = [c.name for c in result.checks if not c.ok]
    assert failed == []
    first, last = result.lines[0], result.lines[-1]
    assert first["start"] == pytest.approx(0.5), "the first caption appears exactly when the first word is said"
    assert last["end"] >= 4.4, "the last word is on screen until it has been said"
    assert last["end"] <= 4.4 + cap.HOLD + 1e-6, "and then disappears"


def test_a_line_gives_way_to_the_next_rather_than_overlapping():
    result = lines_for(SPEECH)
    for a, b in zip(result.lines, result.lines[1:]):
        assert a["end"] <= b["start"] + 1e-6


def test_the_last_caption_is_never_cut_off_by_the_end_of_the_clip():
    # the clip ends 0.1 s after the last word: the hold is shortened, the word is still shown in full
    result = lines_for(SPEECH, [[9.5, 14.0]], 4.5)
    assert result.lines[-1]["end"] == pytest.approx(4.5)
    assert next(c for c in result.checks if c.name == "last_word_shown_in_full").ok


def test_a_caption_low_enough_to_hit_youtubes_buttons_is_flagged():
    result = lines_for(SPEECH, position=83, size=150)
    assert not next(c for c in result.checks if c.name == "clear_of_platform_buttons").ok
    assert not result.passed


def test_unsure_words_are_listed_not_changed():
    words = [w(0.0, 0.4, "headshot"), w(0.5, 0.9, "Skibidi", p=0.21), w(1.0, 1.4, "nice.")]
    result = lines_for(words, [[0, 2]], 2.0)
    assert result.flagged == [{"t": 0.5, "word": "Skibidi", "confidence": 0.21}]
    assert [x["w"] for ln in result.lines for x in ln["words"]] == ["headshot", "Skibidi", "nice."]


def test_no_speech_means_no_captions_and_no_failure():
    result = lines_for([], [[0, 5]], 5.0)
    assert result.ass == "" and result.lines == [] and result.passed


def test_captions_can_be_turned_off():
    result = build_captions(SPEECH, [[9.5, 15.5]], 6.0, CaptionStyle(enabled=False))
    assert result.ass == "" and result.word_count == 10


# ---- the highlight follows the voice ------------------------------------------------


def test_the_highlighted_word_is_the_one_being_said():
    result = lines_for(SPEECH)
    assert highlighted_at(result.lines, 0.55) == "Why"
    assert highlighted_at(result.lines, 1.0) == "is", "'that' starts at 1.05 s"
    assert highlighted_at(result.lines, 1.1) == "that"
    assert highlighted_at(result.lines, 1.9) == "opening?"
    assert highlighted_at(result.lines, 2.3) == "opening?", "held on the last word until the line ends"
    assert highlighted_at(result.lines, 3.0) is None, "the pause before 'I' (said at 3.1 s) shows nothing"
    assert highlighted_at(result.lines, 3.15) == "I"
    assert highlighted_at(result.lines, 0.1) is None, "nothing on screen before anyone speaks"


def test_ass_has_one_event_per_word_with_only_that_word_coloured():
    result = lines_for(SPEECH)
    events = [line for line in result.ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 10
    yellow = cap._ass_colour("#FFE11A")
    assert all(e.count(yellow) == 1 for e in events)
    assert "Dialogue: 0,0:00:00.50,0:00:00.85," in events[0] and "\\c" + yellow + "}WHY" in events[0]
    assert "PlayResX: 1080" in result.ass and "PlayResY: 1920" in result.ass


def test_shouted_lines_use_the_emphasis_colour_in_the_gaming_style():
    result = lines_for(SPEECH, preset="gaming")
    red = cap._ass_colour("#FF3B30")
    events = [line for line in result.ass.splitlines() if line.startswith("Dialogue:")]
    assert all(red not in e for e in events[:5]), "a question is not shouted"
    assert all(red in e for e in events[8:]), "'THE BRIDGE!' is"


def test_text_cannot_break_out_into_ass_commands():
    words = [w(0.0, 0.4, "{\\an8}nope"), w(0.5, 0.9, "back\\slash")]
    ass = lines_for(words, [[0, 2]], 2.0).ass
    event = [line for line in ass.splitlines() if line.startswith("Dialogue:")][0]
    assert "{\\an8}" not in event and "\\slash" not in event


# ---- styles -----------------------------------------------------------------------


def test_every_preset_resolves_and_overrides_win():
    for name in cap.PRESETS:
        assert CaptionStyle(preset=name).resolve().font in cap.FONTS
    style = CaptionStyle(preset="viral", size=70, highlight_color="#00FF00", position=60).resolve()
    assert (style.size, style.highlight_color, style.position, style.font) == (70, "#00FF00", 60, "Montserrat ExtraBold")


@pytest.mark.parametrize(
    "bad",
    [{"font": "Comic Sans MS"}, {"text_color": "yellow"}, {"size": 10}, {"position": 95}, {"preset": "tiktok"}],
)
def test_bad_style_values_are_refused(bad):
    with pytest.raises(ValidationError):
        CaptionStyle(**bad)


# ---- rendering: the highlight really lands on the spoken word ----------------------


def frame_at(settings, video, t, width=540, height=960):
    raw = subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-ss", f"{t:.3f}",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)


def yellow_mask(img):
    r, g, b = (img[..., i].astype(int) for i in range(3))
    return (r > 200) & (g > 170) & (b < 110)


@pytest.mark.parametrize("preset", ["viral", "bold", "minimal", "gaming"])
def test_captions_sit_near_the_middle_clear_of_youtubes_own_controls(preset):
    """The owner's choice (2026-11-17): captions just below the middle, never under YouTube's channel name
    and title at the bottom, nor its like, comment and share buttons down the right-hand side."""
    style = CaptionStyle(preset=preset).resolve()
    assert style.position == 60
    words = [w(0.1 + i * 0.4, 0.4 + i * 0.4, t) for i, t in
             enumerate("unbelievable absolutely extraordinary performance tonight everybody".split())]
    result = build_captions(words, [[0.0, 4.0]], 4.0, CaptionStyle(preset=preset))
    assert result.passed, [c for c in result.checks if not c.ok]
    resolved = CaptionStyle(preset=preset).resolve()
    for line in result.lines:
        drawn = cap.text_width(line["text"], resolved) * cap.line_fit(
            cap.Line([cap.CWord(x["s"], x["e"], x["w"]) for x in line["words"]], line["start"], line["end"]), resolved)
        right_edge = 1080 / 2 + drawn / 2
        assert right_edge <= 1080 - cap.RIGHT_BUTTONS + 0.5, f"{line['text']!r} reaches the button column"
    if preset == "gaming":  # a word wider than the centre is drawn smaller, and the ASS says so
        assert "\\fscx" in result.ass
    bottom = 1920 * style.position / 100 + style.size * 0.7
    assert bottom <= 1920 * cap.PLATFORM_UI_ZONE, "clear of the channel name and title"


def test_burned_in_highlight_moves_from_word_to_word(settings, tmp_path):
    # a plain dark frame, so the only yellow pixels are the caption's
    video = tmp_path / "plain.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x202020:s=1280x720:r=30:d=3", "-f", "lavfi", "-i", "sine=f=300:d=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    words = [w(0.2, 1.2, "left"), w(1.3, 2.3, "right")]
    result = build_captions(words, [[0.0, 3.0]], 3.0, CaptionStyle(preset="viral", words_per_line=2))
    ass = tmp_path / "c.ass"
    ass.write_text(result.ass, encoding="utf-8")
    out = render_vertical(settings, video, tmp_path / "v.mp4", [(0.0, 3.0)], ass, has_audio=True)

    info = probe_media(settings, out)
    assert (info["width"], info["height"]) == (540, 960) and info["has_audio"]

    caption_row = int(960 * cap.DEFAULT_POSITION / 100)
    band = slice(caption_row - 60, caption_row + 60)
    before = yellow_mask(frame_at(settings, out, 0.1))
    first = yellow_mask(frame_at(settings, out, 0.7))
    second = yellow_mask(frame_at(settings, out, 1.8))
    assert before.sum() == 0, "no caption before the first word"
    assert first[band].sum() > 200 and second[band].sum() > 200, "the highlight is drawn at the caption position"
    assert first.sum() == first[band].sum(), "and nowhere else"
    x_first = np.nonzero(first)[1].mean()
    x_second = np.nonzero(second)[1].mean()
    assert x_first < 270 < x_second, "'LEFT' is yellow while it is said, then 'RIGHT'"


# ---- through the app ----------------------------------------------------------------


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

    return wait_for(done, timeout=120)


def test_every_clip_gets_captions_and_a_vertical_preview(client, settings, sample_video):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video), "rights_status": "own",
                                                 "options": {"min_score": 0, "target_duration": 10}}).json()
    project = wait_ready(client, project["id"])
    assert project["analysis"]["captions"] == {"made": 1, "passed": 1, "flagged_words": 0,
                                               "preset": "viral", "enabled": True}
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert clip["has_vertical"]
    captions = clip["captions"]
    assert captions["passed"] and captions["word_count"] == 2
    assert [ln["text"] for ln in captions["lines"]] == ["HELLO CHAT."]
    assert captions["style"]["preset"] == "viral" and captions["moved"] is None
    assert captions["position"] == cap.DEFAULT_POSITION == 60
    assert clip["framing"]["layout"] == "full" and clip["framing"]["subject"]["kind"] == "none"

    vertical = client.get(f"/api/projects/{project['id']}/clips/{clip['id']}/vertical")
    assert vertical.status_code == 200 and vertical.headers["content-type"] == "video/mp4"
    info = probe_media(settings, settings.project_dir(project["id"]) / "clips" / "clip_01_part1_vertical.mp4")
    assert (info["width"], info["height"]) == (540, 960)


def test_restyling_recaptions_without_recutting_or_asking_the_ai(client, sample_video, fake_ai):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video), "rights_status": "own",
                                                 "options": {"min_score": 0, "target_duration": 10}}).json()
    wait_ready(client, project["id"])
    [before] = client.get(f"/api/projects/{project['id']}/clips").json()
    asked = len(fake_ai.text_tasks) + len(fake_ai.visual_tasks)

    r = client.patch(f"/api/projects/{project['id']}/captions", json={"preset": "minimal", "position": 70})
    assert r.status_code == 200 and r.json()["options"]["caption_style"]["preset"] == "minimal"
    after_project = wait_ready(client, project["id"])
    [after] = client.get(f"/api/projects/{project['id']}/clips").json()

    assert after["id"] == before["id"] and after["start"] == before["start"], "the cut is untouched"
    assert after["captions"]["style"]["preset"] == "minimal"
    assert after["captions"]["lines"][0]["text"] == "hello chat."
    assert after_project["analysis"]["captions"]["preset"] == "minimal"
    jobs = client.get(f"/api/jobs?project_id={project['id']}").json()
    assert [s["name"] for s in jobs[0]["stages"]] == ["captions"]
    assert len(fake_ai.text_tasks) + len(fake_ai.visual_tasks) == asked


def test_bad_caption_styles_are_refused_by_the_api(client):
    project = client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x"}).json()
    assert client.patch(f"/api/projects/{project['id']}/captions", json={"font": "Papyrus"}).status_code == 422
    assert client.patch(f"/api/projects/{project['id']}/captions", json={"highlight_color": "#12"}).status_code == 422
    ok = client.patch(f"/api/projects/{project['id']}/captions", json={"preset": "gaming"})
    assert ok.status_code == 200 and ok.json()["status"] == "needs_rights", "no clips yet, so nothing to re-render"


def test_presets_and_fonts_are_served(client):
    data = client.get("/api/captions/presets").json()
    assert [p["name"] for p in data["presets"]] == ["viral", "bold", "minimal", "gaming"]
    assert data["presets"][0]["label"] == "Viral Highlight"
    font = client.get("/api/fonts/Montserrat-ExtraBold.ttf")
    assert font.status_code == 200 and len(font.content) > 100_000
    assert client.get("/api/fonts/..%5C..%5Cclipforge.db").status_code == 404
    assert client.get("/api/fonts/secrets.txt").status_code == 404
    assert client.get("/api/system/info").json()["fonts_installed"] == 4
