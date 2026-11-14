"""HTTP API: projects, defaults, stats, live events."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipforge.api import system as system_api
from clipforge.app import create_app

from .conftest import wait_for


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


def _wait_project(client, project_id: int, status: str) -> dict:
    return wait_for(
        lambda: (p := client.get(f"/api/projects/{project_id}").json())["status"] == status and p,
        timeout=60,
    )


def test_health(client):
    assert client.get("/api/health").json()["app"] == "clipforge"


def test_local_project_is_probed_and_survives_a_restart(client, settings, sample_video):
    r = client.post("/api/projects", json={"source": "local", "path": str(sample_video)})
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["title"] == "sample clip"
    assert project["source_kind"] == "local"

    ready = _wait_project(client, project["id"], "ready")
    assert ready["media"]["width"] == 640 and ready["media"]["height"] == 360
    assert ready["media"]["video_codec"] == "h264" and ready["media"]["audio_codec"] == "aac"
    assert 1.5 < ready["media"]["duration"] < 2.5
    assert ready["has_thumbnail"] and ready["has_proxy"] and ready["has_audio_file"]
    thumb = client.get(f"/api/projects/{project['id']}/thumbnail")
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"  # JPEG

    stats = client.get("/api/stats").json()
    assert stats["videos_processed"] == 1 and stats["clips_total"] == 0

    # A second app on the same data folder is what a restart looks like.
    with TestClient(create_app(settings, configure_logging=False)) as again:
        listed = again.get("/api/projects").json()
        assert [p["id"] for p in listed] == [project["id"]]
        assert listed[0]["status"] == "ready"


def test_missing_file_is_rejected(client, tmp_path):
    r = client.post("/api/projects", json={"source": "local", "path": str(tmp_path / "nope.mp4")})
    assert r.status_code == 422
    assert "not found" in r.json()["error"].lower()


def test_non_video_file_is_rejected(client, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    r = client.post("/api/projects", json={"source": "local", "path": str(f)})
    assert r.status_code == 422 and "not a supported video type" in r.json()["error"]


def test_broken_video_fails_with_a_reason(client, tmp_path):
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"this is not a video")
    project = client.post("/api/projects", json={"source": "local", "path": str(f)}).json()
    failed = _wait_project(client, project["id"], "failed")
    assert failed["status_detail"]


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.twitch.tv/videos/123", "twitch"),
        ("https://kick.com/video/abc", "kick"),
    ],
)
def test_link_platform_is_detected(client, url, platform):
    r = client.post("/api/projects", json={"source": "url", "url": url, "creator": " Some Streamer "})
    assert r.status_code == 201
    body = r.json()
    assert body["source_kind"] == platform
    assert body["creator"] == "Some Streamer"


@pytest.mark.parametrize("url", ["https://example.com/v", "not a link", "ftp://youtube.com/x", "https://notyoutube.com/x"])
def test_other_links_are_rejected(client, url):
    assert client.post("/api/projects", json={"source": "url", "url": url}).status_code == 422


def test_rights_default_to_unverified(client):
    body = client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x"}).json()
    assert body["rights_status"] == "unverified"


def test_option_limits(client):
    def post(options):
        return client.post("/api/projects", json={"source": "url", "url": "https://youtu.be/x", "options": options})

    assert post({"min_duration": 70, "max_duration": 30}).status_code == 422
    assert post({"categories": ["nonsense"]}).status_code == 422
    assert post({"categories": []}).status_code == 422
    assert post({"max_duration": 300}).status_code == 422
    ok = post({"categories": ["rage", "funny"], "max_clips": 3, "min_score": 80})
    assert ok.status_code == 201
    assert ok.json()["options"]["categories"] == ["funny", "rage"]


def test_defaults_round_trip(client):
    assert client.get("/api/settings/defaults").json()["max_clips"] == 8
    saved = client.put("/api/settings/defaults", json={"max_clips": 12, "min_score": 75, "categories": ["horror"]})
    assert saved.status_code == 200
    again = client.get("/api/settings/defaults").json()
    assert (again["max_clips"], again["min_score"], again["categories"]) == (12, 75, ["horror"])


def test_delete_project_keeps_the_source_video(client, sample_video):
    project = client.post("/api/projects", json={"source": "local", "path": str(sample_video)}).json()
    _wait_project(client, project["id"], "ready")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert sample_video.is_file()


def test_selftest_runs_and_reports_over_websocket(client):
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        job = client.post("/api/jobs/selftest", json={"stage_seconds": 1}).json()
        assert [s["name"] for s in job["stages"]] == ["tools", "render", "storage", "wait_a", "wait_b"]
        seen_running_progress = False
        while True:
            event = ws.receive_json()
            if event["type"] != "job" or event["job"]["id"] != job["id"]:
                continue
            j = event["job"]
            if j["status"] == "running" and 0 < j["progress"] < 1:
                seen_running_progress = True
            if j["status"] in ("done", "failed", "cancelled"):
                break
    assert j["status"] == "done", j
    assert seen_running_progress
    assert all(s["status"] == "done" for s in j["stages"])


def test_pick_file_endpoint_uses_the_dialog(client, monkeypatch):
    monkeypatch.setattr(system_api, "pick_video_file", lambda: r"D:\videos\stream.mp4")
    assert client.post("/api/system/pick-file").json() == {"path": r"D:\videos\stream.mp4"}


def test_unknown_api_route_is_404_not_the_dashboard(client):
    assert client.get("/api/nope").status_code == 404
