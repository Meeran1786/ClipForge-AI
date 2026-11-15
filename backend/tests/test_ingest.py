"""Phase 2: rights gate, link ingestion, preview copy, audio, upgrades."""

from __future__ import annotations

import json
import sqlite3
import wave

import pytest
from fastapi.testclient import TestClient

from clipforge.app import create_app
from clipforge.db import make_engine
from clipforge.ingest import IngestError, IngestRefused
from clipforge.ingest.ytdlp_source import classify_error
from clipforge.media import probe_media
from clipforge.rights import download_blocker

from .conftest import wait_for

LINK = "https://www.youtube.com/watch?v=abc"
PERMITTED = {
    "source": "url",
    "url": LINK,
    "rights_status": "creator_permission",
    "creator": "Some Streamer",
    "rights_note": "https://example.test/clip-policy",
}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def wait_status(client, project_id: int, status: str) -> dict:
    return wait_for(
        lambda: (p := client.get(f"/api/projects/{project_id}").json())["status"] == status and p,
        timeout=60,
    )


def stages_of(client, project_id: int) -> dict[str, dict]:
    job = client.get(f"/api/jobs?project_id={project_id}").json()[0]
    return {s["name"]: s for s in job["stages"]}


# ---- the rights gate --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "creator", "note", "allowed"),
    [
        ("own", None, None, True),
        ("creator_permission", "Streamer", "their clip policy link", True),
        ("licensed", "Studio", "CC BY 3.0", True),
        ("unverified", "Streamer", "they will not mind", False),
        ("creator_permission", None, "policy link", False),
        ("creator_permission", "Streamer", None, False),
        ("creator_permission", "Streamer", "   ", False),
        ("licensed", "  ", "CC BY", False),
        ("something_else", "Streamer", "note", False),
    ],
)
def test_download_gate(status, creator, note, allowed):
    assert (download_blocker(status, creator, note) is None) is allowed


def test_unconfirmed_link_is_saved_but_never_downloaded(client, fake_sources):
    body = client.post("/api/projects", json={"source": "url", "url": LINK, "creator": "Some Streamer"}).json()
    assert body["status"] == "needs_rights"
    assert "confirm that you own" in body["download_blocker"]
    assert client.get(f"/api/jobs?project_id={body['id']}").json() == []
    assert fake_sources["youtube"].metadata_calls == 0
    assert fake_sources["youtube"].download_calls == 0


def test_credit_alone_does_not_unlock_the_download(client, fake_sources):
    """Naming the creator is credit, not permission."""
    body = client.post(
        "/api/projects",
        json={"source": "url", "url": LINK, "creator": "Some Streamer", "rights_note": "credited in caption"},
    ).json()
    assert body["rights_status"] == "unverified" and body["status"] == "needs_rights"
    assert fake_sources["youtube"].download_calls == 0


def test_the_download_stage_itself_refuses_without_a_rights_record(client, settings, fake_sources, sample_video):
    """Even if a job is queued some other way, the stage checks again."""
    fake_sources["youtube"].video = sample_video
    project = client.post("/api/projects", json={"source": "url", "url": LINK}).json()
    client.app.state.ctx.worker.enqueue("ingest_url", project_id=project["id"])
    failed = wait_status(client, project["id"], "failed")
    assert "confirm that you own" in failed["status_detail"]
    assert fake_sources["youtube"].metadata_calls == 0
    assert fake_sources["youtube"].download_calls == 0
    assert not (settings.project_dir(project["id"]) / "source.mp4").exists()


# ---- a permitted link, end to end -------------------------------------------


def test_permitted_link_is_downloaded_and_prepared(client, settings, fake_sources, sample_video):
    fake_sources["youtube"].video = sample_video
    project = client.post("/api/projects", json=PERMITTED).json()
    assert project["status"] == "processing" and project["download_blocker"] is None

    ready = wait_status(client, project["id"], "ready")
    assert ready["title"] == "Original Stream Title", "placeholder name is replaced by the real title"
    assert ready["creator"] == "Some Streamer", "the user's credit is kept"
    assert ready["source_meta"]["creator"] == "Channel Name"
    assert ready["source_meta"]["upload_date"] == "2026-01-02"
    assert ready["has_proxy"] and ready["has_audio_file"] and ready["has_thumbnail"]
    stages = stages_of(client, project["id"])
    assert [s["status"] for s in stages.values()] == ["done"] * 12
    assert list(stages)[-3:] == ["moments", "clips", "captions"]
    # each finished stage keeps its closing summary, not a mid-run message
    assert stages["proxy"]["message"].startswith("Preview copy:")
    assert stages["audio"]["message"].startswith("Audio:")

    folder = settings.project_dir(project["id"])
    assert (folder / "source.mp4").is_file()

    proxy = probe_media(settings, folder / "proxy.mp4")
    assert proxy["height"] == 360 and proxy["video_codec"] == "h264" and proxy["audio_codec"] == "aac"
    assert abs(proxy["duration"] - 2.0) < 0.3

    with wave.open(str(folder / "audio.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert abs(w.getnframes() / 16000 - 2.0) < 0.3

    record = json.loads((folder / "rights.json").read_text(encoding="utf-8"))
    assert record["source"]["url"] == LINK
    assert record["source"]["channel"] == "Channel Name"
    assert record["credit"] == "Some Streamer"
    assert record["rights_status"] == "creator_permission" and record["may_publish"] is True
    assert record["permission_evidence"] == "https://example.test/clip-policy"


def test_a_name_the_user_typed_is_not_overwritten(client, fake_sources, sample_video):
    fake_sources["youtube"].video = sample_video
    project = client.post("/api/projects", json={**PERMITTED, "title": "My GTA highlights"}).json()
    assert wait_status(client, project["id"], "ready")["title"] == "My GTA highlights"


def test_confirming_rights_later_starts_the_download(client, fake_sources, sample_video):
    fake_sources["twitch"].video = sample_video
    project = client.post(
        "/api/projects", json={"source": "url", "url": "https://www.twitch.tv/videos/123"}
    ).json()
    assert project["status"] == "needs_rights"

    still = client.patch(
        f"/api/projects/{project['id']}/rights",
        json={"rights_status": "creator_permission", "creator": "Streamer"},
    ).json()
    assert still["status"] == "needs_rights" and "where the permission comes from" in still["download_blocker"]
    assert fake_sources["twitch"].download_calls == 0

    started = client.patch(
        f"/api/projects/{project['id']}/rights",
        json={"rights_status": "creator_permission", "creator": "Streamer", "rights_note": "DM, 3 Nov"},
    ).json()
    assert started["status"] == "processing"
    assert wait_status(client, project["id"], "ready")["source_kind"] == "twitch"
    assert fake_sources["twitch"].download_calls == 1


def test_own_footage_needs_no_creator_or_evidence(client, fake_sources, sample_video):
    fake_sources["youtube"].video = sample_video
    project = client.post("/api/projects", json={"source": "url", "url": LINK, "rights_status": "own"}).json()
    assert project["status"] == "processing"
    wait_status(client, project["id"], "ready")


# ---- refusals and failures --------------------------------------------------


def test_a_refused_video_fails_once_with_the_reason_and_a_way_forward(client, fake_sources):
    fake_sources["youtube"].refuse = IngestRefused("This video is for members or subscribers only.", "members")
    project = client.post("/api/projects", json=PERMITTED).json()
    failed = wait_status(client, project["id"], "failed")
    assert "members or subscribers only" in failed["status_detail"]
    assert "Use a file from this computer" in failed["status_detail"]
    assert fake_sources["youtube"].metadata_calls == 1, "a refusal is final: no retries"
    assert fake_sources["youtube"].download_calls == 0


def test_retrying_a_failed_job_puts_the_project_back_in_processing(client, fake_sources, sample_video):
    """Found in live testing: after Retry the project still said Failed while the job ran."""
    fake_sources["youtube"].refuse = IngestRefused("YouTube blocked automated access to this video.", "blocked")
    project = client.post("/api/projects", json=PERMITTED).json()
    wait_status(client, project["id"], "failed")
    job = client.get(f"/api/jobs?project_id={project['id']}").json()[0]

    fake_sources["youtube"].refuse = None
    fake_sources["youtube"].video = sample_video
    client.app.state.ctx.worker.stop()  # hold the queue so the in-between state can be seen
    client.post(f"/api/jobs/{job['id']}/retry")
    held = client.get(f"/api/projects/{project['id']}").json()
    assert held["status"] == "processing" and held["status_detail"] is None

    client.app.state.ctx.worker.start()
    assert wait_status(client, project["id"], "ready")["has_proxy"]


def test_network_trouble_is_retried(client, fake_sources, sample_video):
    fake_sources["youtube"].video = sample_video
    fake_sources["youtube"].transient_failures = 2
    project = client.post("/api/projects", json=PERMITTED).json()
    wait_status(client, project["id"], "ready")
    assert fake_sources["youtube"].metadata_calls == 3
    assert stages_of(client, project["id"])["metadata"]["attempts"] == 3


def test_not_enough_disk_space_is_refused_before_downloading(client, fake_sources, sample_video):
    fake_sources["youtube"].video = sample_video
    fake_sources["youtube"].est_bytes = 10**15
    project = client.post("/api/projects", json=PERMITTED).json()
    failed = wait_status(client, project["id"], "failed")
    assert "Not enough disk space" in failed["status_detail"]
    assert fake_sources["youtube"].download_calls == 0


def test_a_file_can_be_attached_to_a_link_project(client, settings, fake_sources, sample_video):
    fake_sources["kick"].refuse = IngestRefused("Kick blocked automated access to this video.", "blocked")
    project = client.post("/api/projects", json={**PERMITTED, "url": "https://kick.com/video/abc"}).json()
    wait_status(client, project["id"], "failed")

    attached = client.post(f"/api/projects/{project['id']}/attach", json={"path": str(sample_video)})
    assert attached.status_code == 200
    ready = wait_status(client, project["id"], "ready")
    assert ready["source_kind"] == "kick" and ready["source_url"] == "https://kick.com/video/abc"
    assert ready["creator"] == "Some Streamer"
    assert ready["has_proxy"] and ready["media"]["width"] == 640
    assert fake_sources["kick"].download_calls == 0

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert sample_video.is_file(), "the user's own file is never deleted"
    assert not settings.project_dir(project["id"]).exists()


def test_attach_rejects_a_missing_file(client):
    project = client.post("/api/projects", json={"source": "url", "url": LINK}).json()
    r = client.post(f"/api/projects/{project['id']}/attach", json={"path": r"Z:\nope.mp4"})
    assert r.status_code == 422


def test_unverified_local_file_is_still_processed(client, sample_video):
    """Files the user supplies can always be analysed; only publishing is blocked later."""
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    assert project["rights_status"] == "unverified"
    wait_status(client, project["id"], "ready")


# ---- preview copy and audio -------------------------------------------------


def test_preview_copy_can_be_seeked(client, sample_video):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    whole = client.get(f"/api/projects/{project['id']}/proxy")
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
    part = client.get(f"/api/projects/{project['id']}/proxy", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.content == whole.content[100:200]


def test_video_without_sound_is_prepared_without_audio(client, settings, tmp_path):
    import subprocess

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=s=320x240:r=24:d=1", "-c:v", "libx264", "-preset", "ultrafast", str(silent)],
        check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    project = client.post("/api/projects", json={"source": "local", "path": str(silent)}).json()
    ready = wait_status(client, project["id"], "ready")
    assert ready["has_proxy"] and not ready["has_audio_file"]
    assert ready["media"]["has_audio"] is False


def test_no_unfinished_files_are_left_behind(client, settings, sample_video):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    wait_status(client, project["id"], "ready")
    names = {p.name for p in settings.project_dir(project["id"]).iterdir()}
    assert names == {
        "thumbnail.jpg", "proxy.mp4", "audio.wav", "rights.json",
        "audio_signals.json", "video_signals.json", "clips",
    }
    clip_files = {p.name for p in (settings.project_dir(project["id"]) / "clips").iterdir()}
    assert clip_files == {"clip_01_part1.mp4", "clip_01_part1.ass", "clip_01_part1_vertical.mp4"}, (
        "one plain preview, its captions and the captioned vertical preview; no half-written files"
    )


# ---- error wording ----------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
        ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", "age"),
        ("ERROR: [youtube] abc: Sign in to confirm you're not a bot.", "bot_check"),
        ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "members"),
        ("ERROR: [twitch:vod] 123: This video is only available to subscribers", "members"),
        ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo"),
        ("ERROR: [youtube] abc: This video is DRM protected", "drm"),
        ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "gone"),
        ("ERROR: [kick:vod] abc: Unable to download JSON metadata: HTTP Error 403: Forbidden", "blocked"),
        ("ERROR: Unsupported URL: https://www.youtube.com/feed", "unsupported"),
        ("ERROR: [youtube] 00000000000: This video is unavailable", "gone"),
        ("ERROR: [kick:vod] abc: NOT_FOUND: Requested resource not found", "gone"),
        ("ERROR: [youtube:tab] @Blender: This channel does not have a videos tab", "playlist"),
    ],
)
def test_platform_errors_become_final_refusals(message, code):
    err = classify_error(message, "YouTube")
    assert isinstance(err, IngestRefused) and err.code == code
    assert "does not sign in or work around" in str(err)


def test_unknown_errors_stay_retryable():
    err = classify_error("ERROR: [youtube] abc: Unable to download webpage: timed out", "YouTube")
    assert isinstance(err, IngestError) and not isinstance(err, IngestRefused)
    assert not getattr(err, "no_retry", False)


# ---- upgrading an older database --------------------------------------------


def test_a_phase_1_database_gains_the_new_columns(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE projects (id INTEGER PRIMARY KEY, title VARCHAR(300) NOT NULL, "
        "source_kind VARCHAR(20) NOT NULL, source_url TEXT, source_path TEXT, creator VARCHAR(200), "
        "rights_status VARCHAR(30) NOT NULL, rights_note TEXT, options JSON NOT NULL, media JSON, "
        "thumbnail TEXT, status VARCHAR(30) NOT NULL, status_detail TEXT, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    )
    con.execute(
        "INSERT INTO projects VALUES (1, 'old project', 'local', NULL, 'D:/v.mp4', NULL, 'own', NULL, "
        "'{}', NULL, NULL, 'ready', NULL, '2026-11-14 10:00:00', '2026-11-14 10:00:00')"
    )
    con.commit()
    con.close()

    engine = make_engine(db)
    engine.dispose()

    con = sqlite3.connect(db)
    columns = {row[1] for row in con.execute("PRAGMA table_info(projects)")}
    assert {"source_meta", "proxy_path", "audio_path"} <= columns
    assert con.execute("SELECT title, proxy_path FROM projects").fetchone() == ("old project", None)
    con.close()
