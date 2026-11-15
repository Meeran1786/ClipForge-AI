"""Phase 5 through the app: cutting clips, previews, re-cutting, review."""

from __future__ import annotations

import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipforge.analysis.transcript import Segment, Word
from clipforge.app import create_app
from clipforge.media import probe_media, render_ranges
from clipforge.moments import SUBSCORES, length_bounds

from .conftest import wait_for

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SR = 16000

# A 40 s "stream": five dull lines, then a three-line moment (24.0-34.0 s), then a sign-off.
STORY = [
    (1.0, 3.0, "Let me sort out my inventory first."),
    (5.0, 7.0, "I have twelve bandages and a sandwich."),
    (9.0, 11.0, "The settings look fine to me."),
    (13.0, 15.0, "We need to drive north."),
    (17.0, 19.0, "Nothing happening on this road."),
    (24.0, 27.0, "Why is that bridge opening right now?"),
    (27.5, 30.5, "I am on the bridge and it is opening!"),
    (31.0, 34.0, "I am in the river in an ice cream truck."),
    (37.0, 38.5, "Okay, respawning."),
]


def story_segments() -> list[Segment]:
    out = []
    for start, end, text in STORY:
        tokens = text.split()
        step = (end - start) / len(tokens)
        words = [Word(round(start + k * step, 2), round(start + (k + 1) * step - 0.05, 2), t)
                 for k, t in enumerate(tokens)]
        out.append(Segment(start, end, text, words))
    return out


@pytest.fixture
def story_video(settings, tmp_path):
    """A 40 s video whose sound is silent between the lines, so pauses are measurable."""
    audio = np.zeros(40 * SR, dtype=np.float32)
    t = np.arange(SR) / SR
    for start, end, _ in STORY:
        n = int((end - start) * SR)
        audio[int(start * SR):int(start * SR) + n] = 0.2 * np.sin(2 * np.pi * 300 * np.arange(n) / SR)
    del t
    wav = tmp_path / "story.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    video = tmp_path / "story.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=40", "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    return video


@pytest.fixture
def story(fake_stt, fake_ai):
    """The speech stand-in returns the story; the AI stand-in picks lines 6-8 as the moment."""
    fake_stt.transcribe = lambda audio, language, on_progress=None: (story_segments(), "en")
    fake_ai.text_answer = lambda numbers: {"moments": [{
        "start_line": 6, "end_line": 8, "peak_line": 7, "category": "funny",
        "scores": {k: 8 for k in SUBSCORES}, "reason": "The bridge opens under him.",
    }]}
    return fake_ai


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def wait_ready(client, project_id: int) -> dict:
    def done():
        jobs = client.get(f"/api/jobs?project_id={project_id}").json()
        if not jobs or jobs[0]["status"] in ("queued", "running"):
            return None
        project = client.get(f"/api/projects/{project_id}").json()
        return project if project["status"] != "processing" else None

    return wait_for(done, timeout=120)


def make(client, video, **options) -> dict:
    project = client.post(
        "/api/projects", json={"source": "local", "path": str(video), "rights_status": "own", "options": options}
    ).json()
    return wait_ready(client, project["id"])


# ---- cutting -------------------------------------------------------------------------


def test_a_moment_becomes_a_clip_cut_on_whole_sentences(client, settings, story, story_video):
    project = make(client, story_video, target_duration=10, duration_mode="smart", min_score=0)
    assert project["status"] == "ready" and project["status_detail"] is None
    assert project["analysis"]["clips"] == {
        "made": 1, "not_valid": 0, "duplicates_dropped": 0, "multi_part_moments": 0,
        "target_seconds": 10.0, "mode": "smart", "split": False,
    }
    assert project["clip_count"] == 1

    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert clip["valid"] and clip["review"] == "pending" and clip["rank"] == 1
    assert clip["part"] == 1 and clip["parts"] == 1 and clip["category"] == "funny"
    report = clip["report"]
    assert report["first_word"] == "Why" and report["last_word"] == "truck."
    assert report["sentence_complete"] and report["question_answered"] and report["last_word_fully_inside"]
    assert report["requested_seconds"] == 10 and report["actual_seconds"] == clip["duration"]
    # starts just before "Why" (24.0) and ends just after "truck." (33.95), never inside a neighbouring line
    assert 23.7 <= clip["start"] <= 24.0 and 33.9 <= clip["end"] <= 34.5
    assert 9.9 <= clip["duration"] <= 11.0

    preview = client.get(f"/api/projects/{project['id']}/clips/{clip['id']}/preview")
    assert preview.status_code == 200 and preview.headers["content-type"] == "video/mp4"
    info = probe_media(settings, settings.project_dir(project["id"]) / "clips" / "clip_01_part1.mp4")
    assert info["duration"] == pytest.approx(clip["duration"], abs=0.25)
    assert info["has_audio"]


def test_changing_the_duration_settings_recuts_without_asking_the_ai(client, story, story_video):
    project = make(client, story_video, target_duration=10, min_score=0)
    asked = len(story.text_tasks) + len(story.visual_tasks)
    first = client.get(f"/api/projects/{project['id']}/clips").json()[0]

    assert first["report"]["mode"] == "smart"
    options = {**project["options"], "target_duration": 10, "duration_mode": "strict", "context": "tight"}
    r = client.patch(f"/api/projects/{project['id']}/options", json=options)
    assert r.status_code == 200 and r.json()["status"] == "processing"
    wait_ready(client, project["id"])
    strict = client.get(f"/api/projects/{project['id']}/clips").json()
    assert len(strict) == 1 and strict[0]["report"]["mode"] == "strict", "the old clip is replaced, not added to"

    client.patch(f"/api/projects/{project['id']}/options", json={**options, "target_duration": 30,
                                                                  "duration_mode": "flexible", "context": "full"})
    after = wait_ready(client, project["id"])
    [flexible] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert flexible["valid"] and flexible["report"]["mode"] == "flexible"
    assert after["analysis"]["clips"]["target_seconds"] == 30.0
    assert len(story.text_tasks) + len(story.visual_tasks) == asked, "cutting never calls the AI"


def test_strict_mode_trims_setup_but_never_pads_with_the_next_beat(client, story, story_video):
    """Found while building: Strict dropped a setup line, then tacked on 'Okay, respawning.' from
    after a three-second pause to sit closer to the target."""
    project = make(client, story_video, target_duration=10, duration_mode="strict", min_score=0, context="tight")
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    # the whole moment is 9.95 s of speech plus breathing room: just over a strict 10 s limit
    assert clip["valid"] and clip["duration"] <= 10.0
    assert clip["report"]["last_word"] == "truck.", "it ends on the payoff, not on the unrelated line after the pause"
    assert clip["end"] < 36.0
    assert clip["report"]["setup_sentences_dropped"] == 1 and clip["report"]["first_word"] == "I"


@pytest.fixture
def one_long_sentence(fake_stt, fake_ai):
    """A moment that is a single 14 s sentence: it cannot be shortened or split without cutting it."""
    text = ("We go in through the roof and down the vent and past the lasers and grab the diamond "
            "and it turns out this is the wrong building.")
    tokens = text.split()
    step = 14.0 / len(tokens)
    words = [Word(round(10.0 + k * step, 2), round(10.0 + (k + 1) * step - 0.05, 2), t) for k, t in enumerate(tokens)]
    fake_stt.transcribe = lambda audio, language, on_progress=None: ([Segment(10.0, 24.0, text, words)], "en")
    fake_ai.text_answer = lambda numbers: {"moments": [{
        "start_line": 1, "end_line": 1, "peak_line": 1, "category": "funny",
        "scores": {k: 8 for k in SUBSCORES}, "reason": "The wrong building.",
    }]}


def test_strict_mode_refuses_when_nothing_complete_fits(client, one_long_sentence, story_video):
    project = make(client, story_video, target_duration=10, duration_mode="strict", min_score=0,
                   split_parts=True, max_parts=3)
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert not clip["valid"]
    assert "No complete version of this moment fits 10 s" in clip["report"]["no_fit"]
    assert "could not be split" in clip["report"]["no_fit"], "one sentence cannot be split either"
    assert clip["report"]["shortest_complete_seconds"] == pytest.approx(14.4, abs=0.6)
    summary = project["analysis"]["clips"]
    assert summary["made"] == 0 and summary["not_valid"] == 1
    assert project["clip_count"] == 0, "a clip that failed its checks is not counted as generated"
    assert project["status"] == "ready", "a moment that does not fit is a result, not a failure"


def test_a_clip_that_failed_its_checks_needs_an_explicit_override(client, one_long_sentence, story_video):
    project = make(client, story_video, target_duration=10, duration_mode="strict", min_score=0)
    [bad] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert not bad["valid"] and bad["has_preview"], "the best attempt is still rendered so it can be judged"
    url = f"/api/projects/{project['id']}/clips/{bad['id']}"

    refused = client.patch(url, json={"review": "approved"})
    assert refused.status_code == 409
    assert "No complete version of this moment fits 10 s" in refused.json()["error"]
    assert "Approve it anyway" in refused.json()["error"]
    assert client.get(f"/api/projects/{project['id']}/clips").json()[0]["review"] == "pending"

    forced = client.patch(url, json={"review": "approved", "override": True}).json()
    assert forced["review"] == "approved" and forced["overridden"] is True
    # rejecting it afterwards clears the override mark
    assert client.patch(url, json={"review": "rejected"}).json()["overridden"] is False


def test_review_decisions_are_saved(client, story, story_video):
    project = make(client, story_video, target_duration=10, min_score=0)
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    url = f"/api/projects/{project['id']}/clips/{clip['id']}"
    assert client.patch(url, json={"review": "approved"}).json()["review"] == "approved"
    assert client.get(f"/api/projects/{project['id']}/clips").json()[0]["overridden"] is False
    assert client.patch(url, json={"review": "rejected"}).json()["review"] == "rejected"
    assert client.patch(url, json={"review": "maybe"}).status_code == 422
    assert client.patch(f"/api/projects/{project['id']}/clips/99999", json={"review": "approved"}).status_code == 404


def test_cut_again_and_refusals(client, story, story_video):
    project = make(client, story_video, target_duration=10, min_score=0)
    assert client.post(f"/api/projects/{project['id']}/cut").status_code == 200
    wait_ready(client, project["id"])
    assert len(client.get(f"/api/projects/{project['id']}/clips").json()) == 1

    empty = client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x"}).json()
    r = client.post(f"/api/projects/{empty['id']}/cut")
    assert r.status_code == 409 and "nothing to cut" in r.json()["error"]
    assert client.get(f"/api/projects/{empty['id']}/clips").json() == []


def test_a_long_moment_is_cut_into_connected_parts(client, settings, fake_stt, fake_ai, tmp_path):
    # twelve 5 s lines back to back = one 60 s moment; target 20 s with splitting on
    lines = [(2.0 + 5.0 * k, 6.5 + 5.0 * k, f"This is line number {k + 1} of the long story.") for k in range(12)]

    def segments():
        out = []
        for start, end, text in lines:
            tokens = text.split()
            step = (end - start) / len(tokens)
            out.append(Segment(start, end, text, [
                Word(round(start + i * step, 2), round(start + (i + 1) * step - 0.05, 2), t)
                for i, t in enumerate(tokens)
            ]))
        return out

    fake_stt.transcribe = lambda audio, language, on_progress=None: (segments(), "en")
    fake_ai.text_answer = lambda numbers: {"moments": [{
        "start_line": 1, "end_line": 12, "peak_line": 11, "category": "funny",
        "scores": {k: 8 for k in SUBSCORES}, "reason": "A long story.",
    }]}
    video = tmp_path / "long.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=66", "-f", "lavfi", "-i", "sine=f=300:d=66",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    project = make(client, video, target_duration=20, duration_mode="smart", split_parts=True, max_parts=3, min_score=0)
    clips = client.get(f"/api/projects/{project['id']}/clips").json()

    assert [c["part"] for c in clips] == [1, 2, 3] and all(c["parts"] == 3 and c["valid"] for c in clips)
    assert all(c["has_preview"] for c in clips)
    assert project["analysis"]["clips"]["made"] == 3 and project["analysis"]["clips"]["multi_part_moments"] == 1
    for a, b in zip(clips, clips[1:]):
        assert a["end"] <= b["start"], "parts do not overlap"
        assert b["report"]["continues_from_previous"] and not b["report"]["repeats_dialogue"]
    assert clips[0]["report"]["first_word"] == "This" and clips[-1]["report"]["last_word"] == "story."
    assert "Part 1 of 3" in clips[0]["report"]["reasons"][0]
    # the AI was asked for a moment long enough to be worth splitting
    assert "to 66 seconds" in fake_ai.text_tasks[0]


def test_a_moment_with_no_speech_is_cut_by_time(client, fake_stt, fake_ai, story_video):
    """A jump scare has no dialogue: there are no sentences to respect, so it is cut around the sound."""
    fake_stt.transcribe = lambda audio, language, on_progress=None: (
        [Segment(1.0, 2.0, "Hello.", [Word(1.0, 2.0, "Hello.")])], "en"
    )
    fake_ai.text_answer = lambda numbers: {"moments": []}
    project = client.post("/api/projects", json={"source": "local", "path": str(story_video),
                                                 "options": {"target_duration": 10, "min_score": 0}}).json()
    wait_ready(client, project["id"])
    # add a moment by hand where nobody speaks, as the sound-only path would
    ctx = client.app.state.ctx
    from clipforge.models import Moment

    with ctx.session() as s:
        s.add(Moment(project_id=project["id"], start=20.0, end=30.0, peak=22.0, core_start=20.0, core_end=30.0,
                     category="horror", score=70, subscores={}, source="signals", rank=1, selected=True))
        s.commit()
    client.post(f"/api/projects/{project['id']}/cut")
    wait_ready(client, project["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert clip["valid"] and (clip["start"], clip["end"]) == (20.0, 30.0)
    assert "cut by time" in clip["report"]["reasons"][0] and clip["report"]["sentence_complete"] is None


# ---- the renderer -------------------------------------------------------------------


def test_kept_ranges_are_joined_and_the_gap_between_them_is_gone(settings, story_video, tmp_path):
    out = render_ranges(settings, story_video, tmp_path / "out.mp4", [(1.0, 3.0), (6.0, 8.5)])
    info = probe_media(settings, out)
    assert info["duration"] == pytest.approx(4.5, abs=0.2)
    assert info["video_codec"] == "h264" and info["audio_codec"] == "aac"
    assert not (tmp_path / "out.part.mp4").exists()


# ---- what the AI is asked for --------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"target_duration": 45, "duration_mode": "smart"}, (22.0, 49.0)),
        ({"target_duration": 45, "duration_mode": "strict"}, (22.0, 45.0)),
        ({"target_duration": 45, "duration_mode": "flexible"}, (22.0, 72.0)),
        ({"target_duration": 45, "duration_mode": "smart", "split_parts": True, "max_parts": 3}, (22.0, 148.0)),
        ({"target_duration": 15, "duration_mode": "smart"}, (8.0, 16.0)),
        ({"min_duration": 20, "max_duration": 60}, (20.0, 60.0)),  # a project from before Smart Duration
    ],
)
def test_the_moment_length_asked_of_the_ai_follows_the_duration_settings(options, expected):
    assert length_bounds(options) == expected
