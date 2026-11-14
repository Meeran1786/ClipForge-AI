"""Phase 3: sound and picture signals, chunked transcription, analysis API."""

from __future__ import annotations

import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipforge.analysis.audio_signals import analyse_audio
from clipforge.analysis.transcript import Segment, Word, offset_segments, plan_chunks, tighten
from clipforge.analysis.video_signals import analyse_video
from clipforge.app import create_app

from .conftest import wait_for

SR = 16000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def write_wav(path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def tone(seconds: float, amplitude: float, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def quiet(seconds: float, seed: int = 0) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 0.0005).astype(np.float32)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def wait_status(client, project_id: int, status: str) -> dict:
    return wait_for(
        lambda: (p := client.get(f"/api/projects/{project_id}").json())["status"] == status and p,
        timeout=90,
    )


# ---- sound ------------------------------------------------------------------


def test_silences_and_a_sudden_shout_are_found(tmp_path):
    # 0-20 s steady talking level, 20-24 s silence, 24-26 s shout, 26-40 s talking
    audio = np.concatenate([tone(20, 0.05), quiet(4), tone(2, 0.8, 900), tone(14, 0.05)])
    path = tmp_path / "a.wav"
    write_wav(path, audio)

    seen: list[float] = []
    result = analyse_audio(path, seen.append)

    assert result["duration"] == pytest.approx(40.0, abs=0.01)
    assert len(result["loudness"]) == 80  # one value per half second
    assert seen and seen[-1] <= 1.0

    assert len(result["silences"]) == 1
    start, end = result["silences"][0]
    assert start == pytest.approx(20.0, abs=0.2) and end == pytest.approx(24.0, abs=0.2)

    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["t"] == pytest.approx(24.5, abs=0.6)
    assert event["kind"] == "surge_after_silence"
    assert event["rise_db"] > 15


def test_steady_sound_has_no_events(tmp_path):
    path = tmp_path / "steady.wav"
    write_wav(path, tone(30, 0.1))
    result = analyse_audio(path)
    assert result["events"] == [] and result["silences"] == []


def test_a_loud_moment_without_silence_before_is_a_plain_surge(tmp_path):
    path = tmp_path / "b.wav"
    write_wav(path, np.concatenate([tone(20, 0.03), tone(1.5, 0.7), tone(10, 0.03)]))
    events = analyse_audio(path)["events"]
    assert [e["kind"] for e in events] == ["surge"]
    assert events[0]["t"] == pytest.approx(20.5, abs=0.6)


# ---- picture ----------------------------------------------------------------


def test_scene_changes_are_found(settings, tmp_path):
    video = tmp_path / "cuts.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x180:r=24:d=4",
         "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=3",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=5",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0", "-c:v", "libx264", "-preset", "ultrafast",
         str(video)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    result = analyse_video(settings, video, 12.0)
    assert result["cuts"] == pytest.approx([4.0, 7.0], abs=0.3)
    assert len(result["motion"]) == pytest.approx(24, abs=1)
    # the moving test pattern at the end shows motion; the flat colours do not
    assert max(result["motion"][16:]) > max(result["motion"][1:7])


# ---- chunk planning ---------------------------------------------------------


def test_short_audio_is_one_chunk():
    assert plan_chunks(95.0, [], 0.5, 600) == [(0.0, 95.0)]
    assert plan_chunks(0.0, [], 0.5, 600) == []


def test_chunks_are_cut_at_the_quietest_moment_nearby():
    hop = 0.5
    loudness = [-20.0] * int(1500 / hop)
    loudness[int(612 / hop)] = -70.0  # a pause 12 s after the ideal 600 s edge
    loudness[int(1195 / hop)] = -70.0
    chunks = plan_chunks(1500.0, loudness, hop, 600)
    assert [round(a) for a, _ in chunks] == [0, 612, 1195]
    assert chunks[-1][1] == 1500.0
    assert all(b > a for a, b in chunks)
    assert [a for a, _ in chunks[1:]] == [b for _, b in chunks[:-1]], "chunks leave no gaps"


def test_chunk_times_are_moved_onto_the_video_clock():
    seg = Segment(1.0, 2.5, "hi", [Word(1.0, 1.4, "hi", 0.9)])
    rows = offset_segments([seg], 600.0, 610.0)
    assert rows[0]["start"] == 601.0 and rows[0]["end"] == 602.5
    assert rows[0]["words"] == [{"s": 601.0, "e": 601.4, "w": "hi", "p": 0.9}]


def test_a_line_stretched_back_across_a_pause_is_pulled_to_where_speech_starts():
    """Seen in live testing: 'Three, two, one, jump' was timed 9 s early."""
    rows = [
        {"start": 76.9, "end": 79.9, "text": "never again", "words": [{"s": 76.9, "e": 79.9, "w": "never", "p": 0.9}]},
        {
            "start": 79.9, "end": 91.5, "text": "3, 2, 1, jump.",
            "words": [
                {"s": 79.9, "e": 89.6, "w": "3,", "p": 0.8},
                {"s": 89.9, "e": 90.3, "w": "2,", "p": 0.9},
                {"s": 90.6, "e": 91.5, "w": "jump.", "p": 0.9},
            ],
        },
    ]
    out = tighten(rows, silences=[[80.1, 89.1]])
    assert out[1]["start"] == pytest.approx(89.1, abs=0.3)
    assert out[1]["words"][0]["s"] >= 88.8
    assert out[1]["end"] == 91.5


def test_the_silence_check_alone_fixes_a_line_with_no_word_timings():
    rows = [{"start": 80.0, "end": 92.0, "text": "jump", "words": []}]
    assert tighten(rows, silences=[[80.1, 89.1]])[0]["start"] == 89.1


def test_normally_timed_lines_are_left_alone():
    rows = [{"start": 17.1, "end": 19.9, "text": "wait", "words": [{"s": 17.1, "e": 17.5, "w": "wait", "p": 0.9}]}]
    out = tighten(rows, silences=[[8.3, 17.1], [20.0, 29.1]])
    assert out[0]["start"] == 17.1 and out[0]["words"][0]["s"] == 17.1


def test_sentences_after_pauses_are_not_loud_moments(tmp_path):
    """Seen in live testing: every sentence after a pause was flagged; only the shout should be."""
    rng = np.random.default_rng(1)
    parts = []
    for i in range(8):  # eight 4 s sentences at speaking level, each after 8 s of near-silence
        parts.append(quiet(8, seed=i))
        speech = tone(4, 0.08) * (0.6 + 0.4 * rng.random(4 * SR)).astype(np.float32)
        parts.append(speech)
    audio = np.concatenate(parts)
    audio[int(60.5 * SR):int(61.5 * SR)] += tone(1, 0.6, 1200)  # one real shout
    path = tmp_path / "sparse.wav"
    write_wav(path, audio)

    result = analyse_audio(path)
    assert len(result["silences"]) == 8
    assert [round(e["t"]) for e in result["events"]] == [61] or [round(e["t"]) for e in result["events"]] == [60]


# ---- the whole analysis through the app --------------------------------------


def test_a_prepared_video_is_analysed(client, sample_video, fake_stt):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    ready = wait_status(client, project["id"], "ready")

    assert ready["analysis"]["transcript"] == {
        "chunks_done": 1, "lines": 1, "language": "en", "model": "fake-stt", "done": True,
    }
    assert ready["analysis"]["video"] == {"cuts": 0}
    assert ready["analysis"]["audio"]["events"] == 0

    signals = client.get(f"/api/projects/{project['id']}/signals").json()
    assert signals["has_audio"] and signals["has_video"]
    assert signals["duration"] == pytest.approx(2.0, abs=0.2)
    assert len(signals["loudness"]) == 4 and len(signals["motion"]) == 4

    transcript = client.get(f"/api/projects/{project['id']}/transcript").json()
    assert transcript["done"] and transcript["language"] == "en"
    assert [s["text"] for s in transcript["segments"]] == ["hello chat (2.0s chunk)"]
    assert "words" not in transcript["segments"][0]
    with_words = client.get(f"/api/projects/{project['id']}/transcript?words=true").json()
    assert with_words["segments"][0]["words"][0]["w"] == "hello"


def test_transcription_resumes_at_the_unfinished_chunk(settings, tmp_path, fake_stt):
    """A crash in chunk 3 keeps chunks 1 and 2 and does not redo them."""
    settings.transcript_chunk_seconds = 20
    # 70 s of sound with a silent second every 20 s, as a video file
    audio = np.concatenate([tone(19, 0.1), quiet(1), tone(19, 0.1), quiet(1), tone(19, 0.1), quiet(1), tone(10, 0.1)])
    wav = tmp_path / "long.wav"
    write_wav(wav, audio)
    video = tmp_path / "long.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=10:d=70", "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )

    fake_stt.fail_on_call = 3
    with TestClient(create_app(settings, configure_logging=False)) as client:
        project = client.post("/api/projects", json={"source": "local", "path": str(video)}).json()
        # the stage is tried twice: the crash on call 3, then a clean second attempt
        ready = wait_status(client, project["id"], "ready")
        segments = client.get(f"/api/projects/{project['id']}/transcript").json()["segments"]
        job = client.get(f"/api/jobs?project_id={project['id']}").json()[0]

    stage = next(s for s in job["stages"] if s["name"] == "transcript")
    assert stage["attempts"] == 2
    assert len(fake_stt.calls) == 5, "chunks 1 and 2 ran once; chunk 3 twice; chunk 4 once"
    assert ready["analysis"]["transcript"]["chunks_done"] == 4
    assert len(segments) == 4, "no duplicated lines from the retried chunk"
    starts = [s["start"] for s in segments]
    assert starts == sorted(starts)
    assert starts[0] == pytest.approx(0.2) and starts[1] == pytest.approx(19.7, abs=1.0)


def test_analysis_can_be_run_again_from_scratch(client, sample_video, fake_stt):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")

    again = client.post(f"/api/projects/{project['id']}/analyze")
    assert again.status_code == 200 and again.json()["status"] == "processing"
    ready = wait_status(client, project["id"], "ready")
    assert len(client.get(f"/api/projects/{project['id']}/transcript").json()["segments"]) == 1
    assert ready["analysis"]["transcript"]["lines"] == 1
    assert len(fake_stt.calls) == 2


def test_analyse_is_refused_before_the_video_is_prepared(client):
    project = client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x"}).json()
    r = client.post(f"/api/projects/{project['id']}/analyze")
    assert r.status_code == 409 and "not been prepared" in r.json()["error"]


def test_silent_video_skips_sound_analysis_and_transcript(client, settings, tmp_path, fake_stt):
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=s=320x240:r=24:d=2", "-c:v", "libx264", "-preset", "ultrafast", str(silent)],
        check=True, capture_output=True, creationflags=NO_WINDOW,
    )
    project = client.post("/api/projects", json={"source": "local", "path": str(silent)}).json()
    ready = wait_status(client, project["id"], "ready")
    assert fake_stt.calls == []
    assert ready["analysis"]["audio"] is None
    signals = client.get(f"/api/projects/{project['id']}/signals").json()
    assert not signals["has_audio"] and signals["has_video"] and signals["loudness"] == []
    assert client.get(f"/api/projects/{project['id']}/transcript").json()["segments"] == []


def test_long_curves_are_shrunk_for_drawing_but_keep_their_peaks(client, settings, sample_video):
    import json

    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    path = settings.project_dir(project["id"]) / "audio_signals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["loudness"] = [-40.0] * 9000
    data["loudness"][4321] = -3.0
    path.write_text(json.dumps(data), encoding="utf-8")

    signals = client.get(f"/api/projects/{project['id']}/signals?points=500").json()
    assert len(signals["loudness"]) <= 500
    assert max(signals["loudness"]) == -3.0
    assert signals["loudness_hop"] == pytest.approx(0.5 * 18)
