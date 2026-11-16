"""Phase 9: quality checks on the rendered file, titles and descriptions, the credit, and export."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipforge import framing as fr
from clipforge import metadata as md
from clipforge import qc
from clipforge.ai.base import AIError
from clipforge.app import create_app
from clipforge.captions import CaptionStyle, build_captions
from clipforge.media import MixSpec, measure_loudness, probe_media, render_framed

from .conftest import OPENED_FOLDERS, wait_for

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SR = 48000
SPEECH = [(0.5, 2.5), (3.5, 5.5)]


# ---- making test files -------------------------------------------------------------------------


def write_wav(path: Path, audio: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def voice_video(settings, path: Path, seconds: float = 6.0) -> Path:
    """A moving test picture whose sound is a 'voice' (a warbling tone) only while 'speaking'."""
    audio = np.zeros(int(seconds * SR), np.float32)
    for a, b in SPEECH:
        i, j = int(a * SR), int(b * SR)
        t = np.arange(j - i) / SR
        audio[i:j] = 0.2 * np.sin(2 * np.pi * 220 * t) * (0.7 + 0.3 * np.sin(2 * np.pi * 3 * t))
    wav = write_wav(path.with_suffix(".wav"), audio)
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=640x360:r=30:d={seconds}", "-i", str(wav), "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    return path


def music_track(path: Path, seconds: float = 8.0, amp: float = 0.2) -> Path:
    t = np.arange(int(seconds * SR)) / SR
    return write_wav(path, amp * np.sin(2 * np.pi * 523 * t).astype(np.float32))


def render_final(settings, src: Path, dest: Path, music: MixSpec | None = None) -> Path:
    plan = fr.FramePlan("full", 640, 360, {"t": [0.0], "x": [320.0], "y": [180.0], "cuts": []}, zoom=False)
    size, scale = fr.decode_size(640, 360, fr.OUT_W)
    return render_framed(settings, src, dest, [(0.0, 6.0)], fr.drawer(plan, fr.OUT_W, scale), size, None,
                         width=fr.OUT_W, fps=30, final=True, music=music, audio_channels=1, gain_db=0.0,
                         peak_limit=10 ** (-2 / 20))


def ffmpeg(settings, *args: str) -> None:
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                   check=True, capture_output=True, creationflags=NO_WINDOW)


def check_file(settings, path: Path, src: Path, music_used: bool = False) -> dict[str, str]:
    """The app's checks on one file, as {check name: status}."""
    info = probe_media(settings, path)
    scan = qc.scan_file(settings, path, float(info["duration"]), bool(info["has_audio"]))
    loud = measure_loudness(settings, path, [], whole_file=True) if info["has_audio"] else None
    gap = qc.music_gap(settings, path, src, [(0.0, 6.0)], SPEECH, 0.0, 1) if info["has_audio"] else None
    result = qc.run_checks(info=info, scan=scan, clip_duration=6.0, out_size=(1080, 1920), loudness=loud,
                           loudness_ok=None, music_used=music_used, gap=gap, expect_sound=True, captions=None,
                           report={}, valid=True, overridden=False, others=[], rights_status="own")
    return {c["name"]: c["status"] for c in result["checks"]} | {"_passed": result["passed"], "_gap": gap}


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    from clipforge.config import REPO_ROOT, Settings

    s = Settings(_env_file=None, data_dir=tmp_path_factory.mktemp("data"), ffmpeg_dir=REPO_ROOT / "tools" / "ffmpeg")
    if not s.ffmpeg:
        pytest.skip("FFmpeg is not installed")
    folder = tmp_path_factory.mktemp("qc")
    src = voice_video(s, folder / "src.mp4")
    good = render_final(s, src, folder / "good.mp4")
    return s, folder, src, good


# ---- deliberately broken renders are caught ----------------------------------------------------------


def test_a_good_render_passes_every_file_check(made):
    s, _folder, src, good = made
    result = check_file(s, good, src)
    assert result["_passed"], result
    assert all(v == "ok" for k, v in result.items() if not k.startswith("_")), result
    assert result["_gap"]["gap_db"] > 25, "with no music, nothing but the clip's own sound is in the file"


def test_black_bars_are_caught(made):
    s, folder, src, good = made
    bad = folder / "bars.mp4"
    ffmpeg(s, "-i", str(good), "-vf", "scale=1080:1500,pad=1080:1920:0:210", "-c:v", "libx264", "-preset",
           "ultrafast", "-c:a", "copy", str(bad))
    result = check_file(s, bad, src)
    assert result["black_bars"] == "fail" and not result["_passed"]


def test_a_missing_sound_track_is_caught(made):
    s, folder, src, good = made
    bad = folder / "mute.mp4"
    ffmpeg(s, "-i", str(good), "-an", "-c:v", "copy", str(bad))
    result = check_file(s, bad, src)
    assert result["sound_present"] == "fail" and not result["_passed"]


def test_a_silent_sound_track_is_caught(made):
    s, folder, src, good = made
    bad = folder / "silent.mp4"
    ffmpeg(s, "-i", str(good), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-shortest", str(bad))
    result = check_file(s, bad, src)
    assert result["sound_level"] == "fail" and result["long_silence"] == "fail"


def test_music_too_loud_under_the_speech_is_caught_and_quiet_music_passes(made):
    s, folder, src, _good = made
    track = music_track(folder / "track.wav")
    loud = render_final(s, src, folder / "loud_music.mp4", MixSpec(track, gain_db=0.0, channels=1))
    result = check_file(s, loud, src, music_used=True)
    assert result["music_under_speech"] == "fail" and result["_gap"]["gap_db"] < 6
    quiet = render_final(s, src, folder / "quiet_music.mp4", MixSpec(track, gain_db=-26.0, channels=1))
    result = check_file(s, quiet, src, music_used=True)
    assert result["music_under_speech"] == "ok" and result["_gap"]["gap_db"] >= qc.MUSIC_GAP_MIN


def test_black_frames_are_caught(made):
    s, folder, src, good = made
    bad = folder / "black.mp4"
    ffmpeg(s, "-i", str(good), "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,2,3.5)'",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(bad))
    result = check_file(s, bad, src)
    assert result["black_frames"] == "fail"


def test_a_frozen_picture_is_caught(made):
    s, folder, src, good = made
    bad = folder / "frozen.mp4"
    ffmpeg(s, "-i", str(good), "-vf", "trim=end_frame=1,loop=loop=-1:size=1,setpts=N/30/TB", "-t", "6",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(bad))
    result = check_file(s, bad, src)
    assert result["frozen"] == "fail"


def test_wrong_size_and_length_are_caught(made):
    s, folder, src, good = made
    bad = folder / "small.mp4"
    ffmpeg(s, "-i", str(good), "-vf", "scale=720:1280", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
           "-c:a", "aac", str(bad))
    result = check_file(s, bad, src)
    assert result["format"] == "fail" and result["length"] == "fail"


def test_the_score_falls_with_each_problem():
    one = qc.summarise([qc.ok("a", ""), qc.warn("b", ""), qc.fail("c", "")])
    assert one["score"] == 100 - qc.FAIL_COST - qc.WARN_COST and not one["passed"]
    assert qc.summarise([qc.ok("a", ""), qc.warn("b", "")])["passed"]


def test_clip_level_checks_cut_words_duplicates_and_rights():
    info = {"width": 1080, "height": 1920, "video_codec": "h264", "audio_codec": "aac", "duration": 6.0,
            "has_audio": True}
    scan = qc.Scan((270, 480, 0, 0), (270, 480), [], [], [], -20.0, -3.0)
    caps = {"lines": [{"words": [{"w": w} for w in "hello chat how are you".split()]}], "checks": [],
            "word_count": 5, "flagged_words": [{"word": "chat"}, {"word": "how"}]}
    result = qc.run_checks(info=info, scan=scan, clip_duration=6.0, out_size=(1080, 1920),
                           loudness={"true_peak": -2.0}, loudness_ok=(True, ""), music_used=False, gap=None,
                           expect_sound=True, captions=caps, report={"no_fit": "It ends mid-sentence."},
                           valid=False, overridden=False, others=[["hello", "chat", "how", "are", "you"]],
                           rights_status="unverified")
    by = {c["name"]: c for c in result["checks"]}
    assert by["cut_words"]["status"] == "fail" and "mid-sentence" in by["cut_words"]["detail"]
    assert by["duplicate"]["status"] == "warn"
    assert by["caption_confidence"]["status"] == "warn"
    assert by["rights"]["status"] == "warn" and "not confirmed" in by["rights"]["detail"]


# ---- titles and descriptions -------------------------------------------------------------------------


SOURCE = md.Source(title="Big stream", original_title="Day 3 of the run", creator="Some Streamer",
                   channel="Channel Name", platform="twitch", url="https://www.twitch.tv/videos/123",
                   rights_status="creator_permission", language="en")


def test_the_credit_is_written_by_code_first_in_the_description():
    text = md.compose_description("Watch this https://evil.example/x moment", SOURCE, None, ["#a", "#Shorts"], 1, 1)
    lines = text.splitlines()
    assert lines[0] == "Credit: Some Streamer on Twitch"
    assert lines[1] == "Original video: https://www.twitch.tv/videos/123"
    assert "evil.example" not in text, "links only ever come from the credit"
    own = md.compose_description("Mine.", md.Source(**{**SOURCE.__dict__, "rights_status": "own"}), None,
                                 ["#Shorts"], 1, 1)
    assert "Credit" not in own and "twitch.tv" not in own


def test_music_credit_and_part_line_are_added():
    text = md.compose_description("A moment.", SOURCE, "Track by Someone (CC BY 4.0)", ["#Shorts"], 1, 2)
    assert "Part 1 of 2. More in the next part." in text and "Music: Track by Someone (CC BY 4.0)" in text
    assert text.rstrip().endswith("#Shorts")


def test_titles_are_tidied_and_parts_numbered():
    assert md.clean_title("THIS IS INSANE #gaming", 2, 3) == "This is insane (Part 2/3)"
    assert md.clean_title('"A wrapped title"', 1, 1) == "A wrapped title"
    # found in the gate: a title opening with a quotation lost its first quote mark
    assert md.clean_title('"I should just crush you" — the confrontation peaks', 2, 2) == \
        '"I should just crush you" — the confrontation peaks (Part 2/2)'
    long = md.clean_title("word " * 40, 1, 1)
    assert len(long) <= md.TITLE_AIM + 1 and long.endswith("…")


def test_hashtags_are_cleaned_and_shorts_always_last():
    tags = md.clean_hashtags(["#gta 6", "gta6", "#GTA6", "#Shorts", "", "#123", "fun!"], "funny")
    assert tags[-1] == "#Shorts" and tags.count("#gta6") + tags.count("#GTA6") == 1
    assert "#fun" in tags and "#funny" in tags and all(" " not in t for t in tags)
    assert len(md.clean_hashtags([f"t{i}" for i in range(20)], None)) == md.HASHTAGS_MAX


class OneAnswer:
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.tasks = reply, error, []

    def ask_json(self, task, schema, images=None, check=None):
        self.tasks.append(task)
        if self.error:
            raise self.error
        return self.reply


def test_one_request_writes_every_clip_and_a_missing_one_gets_plain_text():
    clips = [md.ClipText(1, "funny", "He trips over the cable.", "oh no the cable", 20.0),
             md.ClipText(2, "exciting", "Last-second win.", "we won we actually won", 30.0, 1, 2)]
    ai = OneAnswer({"clips": [{"id": 1, "title": "He trips over the cable", "description": "Ouch.",
                               "hashtags": ["#fail"], "pinned_comment": "Ever done this?"}]})
    out = md.write(ai, clips, SOURCE, {1: None, 2: None})
    assert len(ai.tasks) == 1 and "Clip 1:" in ai.tasks[0] and "Clip 2:" in ai.tasks[0]
    assert out[1].by == "fake" and out[1].title == "He trips over the cable"
    assert out[2].by == "plain" and out[2].title.endswith("(Part 1/2)")
    assert out[2].description.startswith("Credit: Some Streamer on Twitch")


def test_without_the_ai_plain_text_is_made_and_says_so():
    clips = [md.ClipText(1, "funny", "He trips.", "oh no the cable is everywhere in here", 20.0)]
    out = md.write(OneAnswer(error=AIError("timed out")), clips, SOURCE, {1: None})
    assert out[1].by == "plain" and "timed out" in out[1].notes[0]
    assert out[1].title.startswith("oh no the cable") and out[1].hashtags[-1] == "#Shorts"


def test_the_writer_is_told_only_what_was_said_and_not_to_claim_permission():
    task = md.task([md.ClipText(1, "funny", "A reason.", "the words", 10.0)], SOURCE)
    assert '"the words"' in task and "A reason." in task
    assert "Do not say the clip is official, licensed, fair use or approved" in task


# ---- the creator credit on screen ------------------------------------------------------------------------


def test_the_creator_credit_is_drawn_for_the_whole_clip_off_faces():
    plan = fr.FramePlan("full", 1920, 1080, {"t": [0.0], "x": [960.0], "y": [540.0], "cuts": []}, zoom=False)
    top_left_face = [fr.Sample(i / 5, [fr.Box(700, 0, 150, 160)]) for i in range(20)]
    place = fr.place_text(plan, top_left_face, 76, 88, 7, None, ("@SomeStreamer", 300, 45))
    assert place["tag"]["where"] != "top left" and place["tag"]["face_share"] == 0
    result = build_captions([{"s": 0.5, "e": 0.9, "w": "hi", "p": 0.9}], [[0.0, 4.0]], 4.0, CaptionStyle(),
                            tag=place["tag"])
    assert "Style: Tag," in result.ass and "Dialogue: 1,0:00:00.00,0:00:04.00,Tag,," in result.ass
    assert "@SomeStreamer" in result.ass


# ---- through the app: render, write, check, export ------------------------------------------------------


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


def finished_project(client, video, rights="own", creator=None):
    body = {"source": "local", "path": str(video), "rights_status": rights, "creator": creator,
            "rights_note": "Written permission by email." if rights != "own" else None,
            "options": {"min_score": 0, "target_duration": 10}}
    project = wait_ready(client, client.post("/api/projects", json=body).json()["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}", json={"review": "approved"})
    assert client.post(f"/api/projects/{project['id']}/render-final", json={}).status_code == 200
    project = wait_ready(client, project["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    return project, clip


def test_cutting_and_captioning_never_ask_the_ai_for_text_but_the_final_render_does_once(client, sample_video, fake_ai):
    body = {"source": "local", "path": str(sample_video), "rights_status": "own",
            "options": {"min_score": 0, "target_duration": 10}}
    project = wait_ready(client, client.post("/api/projects", json=body).json()["id"])
    assert fake_ai.meta_tasks == []
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}", json={"review": "approved"})
    client.post(f"/api/projects/{project['id']}/render-final", json={})
    wait_ready(client, project["id"])
    assert len(fake_ai.meta_tasks) == 1
    client.post(f"/api/projects/{project['id']}/render-final", json={})
    wait_ready(client, project["id"])
    assert len(fake_ai.meta_tasks) == 1, "rendering again keeps the text it has"


def test_a_final_short_gets_its_text_and_quality_report(client, sample_video):
    project, clip = finished_project(client, sample_video)
    meta = clip["meta"]
    assert meta["title"] == "Chat gets a warm hello" and meta["by"] == "fake-ai" and not meta["edited"]
    assert meta["hashtags"][-1] == "#Shorts" and "#hello" in meta["hashtags"]
    assert "Credit" not in meta["description"], "the owner's own video needs no credit"
    q = clip["final"]["qc"]
    assert q["passed"] and q["score"] >= 70, q
    names = {c["name"] for c in q["checks"]}
    assert {"format", "black_bars", "sound_present", "peaks", "long_silence", "cut_words", "rights"} <= names
    assert clip["export_blocker"] is None


def test_someone_elses_video_is_credited_on_screen_in_the_text_and_in_the_export(client, sample_video, settings):
    project, clip = finished_project(client, sample_video, rights="creator_permission", creator="Some Streamer")
    assert clip["captions"]["tag"]["text"] == "@SomeStreamer"
    assert clip["meta"]["description"].startswith("Credit: Some Streamer")
    r = client.post(f"/api/projects/{project['id']}/export", json={}).json()
    [done] = r["exported"]
    folder = Path(done["folder"])
    sidecar = json.loads((folder / done["files"][2]).read_text(encoding="utf-8"))
    assert sidecar["rights"]["credit"] == "Some Streamer" and sidecar["may_publish"]
    assert sidecar["rights"]["notice"].startswith("Credit is not permission")


def test_export_writes_the_video_its_text_and_the_rights_record(client, sample_video, settings):
    project, clip = finished_project(client, sample_video)
    pid = project["id"]
    r = client.post(f"/api/projects/{pid}/export", json={})
    assert r.status_code == 200
    [done] = r.json()["exported"]
    folder = Path(done["folder"])
    assert folder.parent == settings.exports_dir
    names = sorted(p.name for p in folder.iterdir())
    assert names == sorted([*done["files"], "rights.json"]), "no half-written files"
    video, text, sidecar = (folder / n for n in done["files"])
    assert video.name.startswith("01_Chat_gets_a_warm_hello") and video.suffix == ".mp4"
    assert probe_media(settings, video)["width"] == 1080
    assert "TITLE\nChat gets a warm hello" in text.read_text(encoding="utf-8")
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["quality"]["passed"] and data["may_publish"] and data["warning"] is None
    assert data["clip"]["from_original"] and data["render"]["width"] == 1080
    [clip] = client.get(f"/api/projects/{pid}/clips").json()
    assert clip["status"] == "exported" and clip["exported"]["video"] == video.name
    assert client.get("/api/stats").json()["clips_exported"] == 1

    # the owner renames it: exporting again replaces the old files rather than leaving both
    edited = {"title": "My own title", "description": "Mine.", "hashtags": ["mine"], "pinned_comment": "Hi?"}
    assert client.patch(f"/api/projects/{pid}/clips/{clip['id']}/meta", json=edited).json()["meta"]["edited"]
    client.post(f"/api/projects/{pid}/export", json={})
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 4 and any(n.startswith("01_My_own_title") for n in names)
    assert not any("Chat_gets" in n for n in names)

    assert client.post(f"/api/system/open-exports?project_id={pid}").json()["folder"] == str(folder)
    assert OPENED_FOLDERS == [folder]


def test_unverified_rights_are_exported_with_a_warning(client, sample_video):
    project, _clip = finished_project(client, sample_video, rights="unverified")
    [done] = client.post(f"/api/projects/{project['id']}/export", json={}).json()["exported"]
    folder = Path(done["folder"])
    data = json.loads((folder / done["files"][2]).read_text(encoding="utf-8"))
    assert not data["may_publish"] and "not confirmed" in data["warning"]
    assert (folder / done["files"][1]).read_text(encoding="utf-8").startswith("WARNING: rights not confirmed")


def test_only_approved_rendered_clips_that_passed_are_exported_unless_forced(client, sample_video, settings):
    from clipforge.db import make_engine, make_session_factory
    from clipforge.models import Clip

    project, clip = finished_project(client, sample_video)
    pid = project["id"]
    engine = make_engine(settings.db_path)
    with make_session_factory(engine)() as s:  # pretend a check failed
        row = s.get(Clip, clip["id"])
        row.final = {**row.final, "qc": {**row.final["qc"], "passed": False, "checks": [
            {"name": "black_bars", "label": "No black bars", "status": "fail", "detail": "bars"}]}}
        s.commit()
    engine.dispose()
    r = client.post(f"/api/projects/{pid}/export", json={}).json()
    assert r["exported"] == [] and "No black bars" in r["refused"][0]["reason"]
    r = client.post(f"/api/projects/{pid}/export", json={"override": True}).json()
    assert len(r["exported"]) == 1
    data = json.loads((Path(r["exported"][0]["folder"]) / r["exported"][0]["files"][2]).read_text(encoding="utf-8"))
    assert data["quality"]["exported_anyway"]

    client.patch(f"/api/projects/{pid}/clips/{clip['id']}", json={"review": "rejected"})
    r = client.post(f"/api/projects/{pid}/export", json={"clip_ids": [clip["id"]], "override": True}).json()
    assert r["exported"] == [] and "approved" in r["refused"][0]["reason"]


def test_write_again_asks_the_ai_and_replaces_edits(client, sample_video, fake_ai):
    project, clip = finished_project(client, sample_video)
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/clips/{clip['id']}/meta", json={"title": "Mine"})
    fake_ai.meta_answer = lambda ids: {"clips": [{"id": i, "title": "A fresh title", "description": "New.",
                                                  "hashtags": ["#new"], "pinned_comment": "?"} for i in ids]}
    assert client.post(f"/api/projects/{pid}/metadata", json={"clip_ids": [clip["id"]]}).status_code == 200
    wait_ready(client, pid)
    [clip] = client.get(f"/api/projects/{pid}/clips").json()
    assert clip["meta"]["title"] == "A fresh title" and not clip["meta"]["edited"]


def test_the_shorts_list_shows_approved_and_rendered_clips_with_their_project(client, sample_video):
    project, clip = finished_project(client, sample_video, rights="licensed", creator="Blender")
    [row] = client.get("/api/shorts").json()
    assert row["id"] == clip["id"] and row["project"] == {"id": project["id"], "title": project["title"],
                                                          "rights_status": "licensed", "creator": "Blender"}
    assert row["final"]["qc"]["score"] >= 0 and "file" not in row["final"]
