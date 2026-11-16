"""Phase 11.1: new categories, the hook score, measured quality, the transcript context check,
duplicates by words, movement bursts, and the flags the picture check sets."""

from __future__ import annotations

import numpy as np
import pytest

from clipforge import moment_quality as mq
from clipforge import moments as m
from clipforge.moments import SUBSCORES, Candidate, Line
from clipforge.music import CATEGORY_MOODS, MOODS
from clipforge.schemas import CATEGORIES, ProjectOptions


def L(n, start, end, text):
    return Line(n, start, end, text)


# ---- categories and scores ---------------------------------------------------------------------


def test_the_new_categories_are_known_explained_and_have_music():
    for c in ("suspense", "celebration", "punchline", "gameplay_outcome"):
        assert c in CATEGORIES and c in m.CATEGORY_MEANING
        assert CATEGORY_MOODS[c] and set(CATEGORY_MOODS[c]) <= set(MOODS)
    assert set(m.CATEGORY_MEANING) == set(CATEGORIES)
    old = ProjectOptions(categories=["funny", "horror"])  # a project saved before the new ones still loads
    assert old.categories == ["funny", "horror"]


def test_the_ai_is_asked_for_a_hook_score_and_told_what_each_category_means():
    task = m.text_task("L1 [0:00] hi", CATEGORIES, 20, 60, 6)
    assert "- hook: how strongly the first seconds grab attention" in task
    assert "gameplay_outcome = how a game moment turns out" in task
    assert "cuts a conversation off mid-exchange" in task
    assert "hook" in m.TEXT_SCHEMA["properties"]["moments"]["items"]["properties"]["scores"]["required"]


def test_scores_are_the_same_every_time_for_the_same_input():
    w = m.clean_weights(None)
    subs = {k: 7 for k in SUBSCORES}
    measured = {"context_code": 6, "audio_quality": 9, "visual_quality": 4}
    assert len({m.viral_score(subs, w, measured) for _ in range(20)}) == 1


def test_a_missing_or_malformed_ai_score_counts_as_zero_and_never_crashes():
    cleaned = m.clean_subscores({"entertainment": "lots", "hook": 99})
    assert cleaned["entertainment"] == 0 and cleaned["hook"] == 10 and set(cleaned) == set(SUBSCORES)
    assert m.viral_score(m.clean_subscores(None), m.clean_weights(None)) == 0


# ---- context completeness from the transcript --------------------------------------------------------


def test_a_moment_with_its_whole_thought_scores_full_context():
    lines = [L(1, 0, 2, "We start here."), L(2, 3.5, 5, "Then the joke."), L(3, 5.2, 7, "And the payoff!"),
             L(4, 9, 10, "Something else.")]
    score, notes = m.context_check(lines, lines[1], lines[2])
    assert score == 10 and notes == []


@pytest.mark.parametrize(("first", "last", "lines", "note"), [
    # the previous line runs straight into the first one, unfinished
    (1, 1, [L(0, 0, 1.9, "and then he"), L(1, 2.0, 4, "fell over."), L(2, 6, 7, "Ok.")], "starts in the middle of a sentence"),
    # the last line is cut off before the sentence finishes
    (0, 0, [L(0, 0, 2, "So what I was going"), L(1, 2.1, 4, "to say is this.")], "ends before the sentence is finished"),
    # a question whose answer comes just after the moment
    (0, 0, [L(0, 0, 2, "Who did it?"), L(1, 3, 4, "I did.")], "ends on a question that is answered just after"),
    # the conversation carries straight on
    (0, 0, [L(0, 0, 2, "That was close."), L(1, 2.1, 4, "Yeah, too close.")], "the conversation carries straight on after it"),
])
def test_incomplete_context_is_found_and_said(first, last, lines, note):
    by = {line.n: line for line in lines}
    score, notes = m.context_check(lines, by[first], by[last])
    assert score < 10 and note in notes


def test_parsed_moments_carry_the_context_check_and_a_no_dialogue_flag():
    chunk = [L(1, 0, 2, "Look."), L(2, 2.5, 4, "Whoa!"), L(3, 6, 7, "Anyway.")]
    answer = {"moments": [{"start_line": 1, "end_line": 2, "peak_line": 2, "category": "funny",
                           "scores": {k: 6 for k in SUBSCORES}, "reason": "r"}]}
    [c] = m.parse_text_moments(answer, chunk, CATEGORIES, 1, 10, 10)
    assert c.measured["context_code"] == 10 and c.flags == {"no_dialogue": True}  # only 2 words are said


# ---- duplicates --------------------------------------------------------------------------------------


def cand(start, end, score, excerpt):
    return Candidate(start=start, end=end, peak=start, category="funny", subscores={k: 5 for k in SUBSCORES},
                     reason="", excerpt=excerpt, score=score)


def test_the_same_story_told_twice_is_kept_once():
    story = "the dragon came out of nowhere and burned the whole village to the ground tonight"
    a = cand(10, 30, 80, story)
    b = cand(500, 520, 70, story + " again")  # far apart in time, same words
    c = cand(900, 920, 60, "a completely different moment about cooking pasta in the kitchen with friends")
    kept = m.drop_duplicates([a, b, c])
    assert kept == [a, c]


def test_moments_that_overlap_in_time_are_still_duplicates_and_short_excerpts_are_not_compared():
    assert m.same_story(cand(0, 20, 50, "x"), cand(5, 25, 40, "y"))
    assert not m.same_story(cand(0, 10, 50, "oh no"), cand(100, 110, 40, "oh no"))


# ---- bursts of movement --------------------------------------------------------------------------------


def test_a_burst_of_movement_nobody_talked_over_becomes_a_candidate():
    motion = [0.01] * 400
    motion[200:204] = [0.2, 0.25, 0.2, 0.15]  # at 100 s with a 0.5 s hop
    out = m.motion_candidates(motion, 0.5, [], [], 20, 40, 200)
    assert len(out) == 1 and out[0].start <= 100.5 <= out[0].end
    assert out[0].source == "signals" and out[0].extra["found_by"] == "motion" and out[0].flags["no_dialogue"]
    assert m.motion_candidates(motion, 0.5, [cand(90, 120, 50, "")], [], 20, 40, 200) == [], "already covered"
    assert m.motion_candidates([0.01] * 400, 0.5, [], [], 20, 40, 200) == [], "nothing stands out"
    assert m.motion_candidates([], 0.5, [], [], 20, 40, 200) == []


# ---- the picture check's flags ----------------------------------------------------------------------------


def test_reaction_and_gameplay_flags_come_only_from_a_useful_picture():
    c = cand(0, 10, 50, "x")
    batch = [(1, c, "a.jpg")]
    m.apply_visual({"moments": [{"id": 1, "seen": "s", "picture_useful": True, "keep": True, "category": "funny",
                                 "scores": {k: 5 for k in SUBSCORES}, "reaction": True, "gameplay": True}]},
                   batch, CATEGORIES)
    assert c.flags == {"reaction": True, "gameplay": True}
    d = cand(0, 10, 50, "x")
    m.apply_visual({"moments": [{"id": 1, "seen": "test pattern", "picture_useful": False, "keep": False,
                                 "category": "funny", "scores": {}, "reaction": True, "gameplay": True}]},
                   [(1, d, "a.jpg")], CATEGORIES)
    assert "reaction" not in d.flags, "a picture that shows nothing cannot vouch for a reaction"


# ---- measured sound quality ------------------------------------------------------------------------------


RATE = 16000


def voice(seconds=6.0, amp=0.2, gaps=True, noise=0.0, seed=0):
    t = np.arange(int(seconds * RATE)) / RATE
    x = amp * np.sin(2 * np.pi * 200 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    if gaps:
        x[(t % 2) > 1.7] = 0  # short pauses, like breathing between phrases
    if noise:
        x = x + noise * np.random.default_rng(seed).standard_normal(t.size)
    return x.astype(np.float32)


def test_clear_speech_scores_high():
    score, detail = mq.audio_quality(voice(), RATE)
    assert score >= 9 and detail["notes"] == []


def test_clipping_silence_quietness_and_background_lower_the_sound_score():
    clean, _ = mq.audio_quality(voice(), RATE)
    clipped, d = mq.audio_quality(np.clip(voice(amp=3.0), -1, 1), RATE)
    assert clipped < clean and any("clips" in n for n in d["notes"])
    mostly_silent = voice()
    mostly_silent[: int(4.5 * RATE)] = 0
    silent, d = mq.audio_quality(mostly_silent, RATE)
    assert silent < clean and any("silent" in n for n in d["notes"])
    quiet, d = mq.audio_quality(voice(amp=0.005), RATE)
    assert quiet < clean and any("quiet" in n for n in d["notes"])
    noisy, d = mq.audio_quality(voice(gaps=False, noise=0.15), RATE)
    assert noisy < clean and any("background" in n for n in d["notes"])


def test_too_short_to_measure_says_so():
    assert mq.audio_quality(np.zeros(100, np.float32), RATE)[1]["notes"] == ["too short to measure"]


# ---- measured picture quality ----------------------------------------------------------------------------


def textured(seed=0, brightness=128):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (180, 320)).astype(np.float32)
    img = (base - base.mean()) * 0.5 + brightness
    return np.clip(img, 0, 255).astype(np.uint8)


def test_a_sharp_well_lit_moving_picture_scores_high():
    score, detail = mq.picture_quality([textured(i) for i in range(5)], still_share=0.0)
    assert score == 10 and detail["notes"] == []


def test_blur_darkness_black_frames_and_a_still_picture_lower_the_picture_score():
    import cv2

    blurred, d = mq.picture_quality([cv2.GaussianBlur(textured(i), (0, 0), 6) for i in range(5)], 0.0)
    assert blurred < 10 and "blurry" in d["notes"] or "a little soft" in d["notes"]
    dark, d = mq.picture_quality([textured(i, brightness=20) for i in range(5)], 0.0)
    assert dark < 10 and "very dark" in d["notes"]
    black, d = mq.picture_quality([np.zeros((180, 320), np.uint8)] * 2 + [textured(i) for i in range(3)], 0.0)
    assert black < 10 and any("black picture" in n for n in d["notes"])
    still, d = mq.picture_quality([textured(0)] * 5, still_share=0.9)
    assert still <= 7 and any("stands still" in n for n in d["notes"])
    assert mq.picture_quality([], None) == (0, {"notes": ["no frames could be read"]})


def test_still_share_comes_from_the_motion_curve():
    motion = [0.0] * 10 + [0.05] * 10
    assert mq.still_share(motion, 0.5, 0, 5) == 1.0
    assert mq.still_share(motion, 0.5, 5, 10) == 0.0
    assert mq.still_share([], 0.5, 0, 5) is None


def test_measuring_real_files_gives_both_scores(settings, sample_video, tmp_path):
    import subprocess

    wav = tmp_path / "a.wav"
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(sample_video),
                    "-ac", "1", "-ar", "16000", str(wav)], check=True, capture_output=True)
    out = mq.measure(settings, wav, sample_video, [0.05] * 4, 0.5, 0.0, 2.0)
    assert 0 <= out["audio_quality"] <= 10 and 0 <= out["visual_quality"] <= 10
    assert "notes" in out["audio_detail"] and "sharpness" in out["visual_detail"]
    assert mq.measure(settings, None, None, [], 0.5, 0.0, 2.0) == {}, "nothing to measure: nothing claimed"


# ---- through the API -----------------------------------------------------------------------------------


def test_moments_show_every_score_its_source_and_the_flags(settings, sample_video):
    from fastapi.testclient import TestClient

    from clipforge.app import create_app

    from .test_moments import wait_status

    with TestClient(create_app(settings, configure_logging=False)) as client:
        project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
        wait_status(client, project["id"], "ready")
        [moment] = client.get(f"/api/projects/{project['id']}/moments").json()
        assert set(moment["parts"]) == set(m.SCORE_KEYS)
        assert moment["measured"]["audio_detail"]["notes"] is not None and "context_code" in moment["measured"]
        assert "no_dialogue" in moment["flags"]
        weights = client.get("/api/settings/weights").json()
        assert weights["source"]["audio_quality"] == "measured" and weights["source"]["hook"] == "ai"
        assert "does not predict views" in weights["note"]
        r = client.put("/api/settings/weights", json={"audio_quality": 1})
        assert r.status_code == 200
        [moment] = client.get(f"/api/projects/{project['id']}/moments").json()
        assert moment["score"] == moment["parts"]["audio_quality"] * 10, "only measured sound counts now"
