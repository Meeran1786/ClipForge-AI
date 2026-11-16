"""Phase 10: connecting the owner's channel, private uploads, confirmed publishing, and the rights refusal."""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipforge import youtube as yt
from clipforge.app import create_app

from .test_qc_export import finished_project, wait_ready

CLIENT = {"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "s3cret",
                        "project_id": "my-clips", "redirect_uris": ["http://localhost"]}}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, configure_logging=False)) as c:
        yield c


@pytest.fixture
def client_file(tmp_path) -> Path:
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(CLIENT), encoding="utf-8")
    return path


def connect(client, client_file, fake_google) -> dict:
    assert client.post("/api/youtube/client", json={"path": str(client_file)}).status_code == 200
    url = client.post("/api/youtube/connect").json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    page = client.get(f"/api/youtube/callback?state={q['state']}&code=one-time-code")
    assert page.status_code == 200, page.text
    return q


# ---- the client file and the sign-in --------------------------------------------------------------


def test_only_a_desktop_app_client_file_is_accepted(client, tmp_path):
    web = tmp_path / "web.json"
    web.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}), encoding="utf-8")
    r = client.post("/api/youtube/client", json={"path": str(web)})
    assert r.status_code == 422 and "Desktop app" in r.json()["error"]
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")
    assert client.post("/api/youtube/client", json={"path": str(junk)}).status_code == 422
    assert client.post("/api/youtube/connect").status_code == 409, "no sign-in without a client file"


def test_the_sign_in_uses_googles_page_pkce_and_a_local_return_address(client, client_file, settings, fake_google):
    q = connect(client, client_file, fake_google)
    assert q["client_id"] == CLIENT["installed"]["client_id"]
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/api/youtube/callback")
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert q["scope"] == "https://www.googleapis.com/auth/youtube" and q["access_type"] == "offline"
    status = client.get("/api/youtube").json()
    assert status["connected"] and status["channel"]["title"] == "Test Channel"
    token = json.loads((settings.data_dir / "youtube" / "token.json").read_text(encoding="utf-8"))
    assert token["refresh_token"] == "refresh-1", "kept in the data folder, which is not in git"
    assert "password" not in json.dumps(token).lower()


def test_a_callback_that_was_not_started_here_is_refused(client, client_file, fake_google):
    client.post("/api/youtube/client", json={"path": str(client_file)})
    page = client.get("/api/youtube/callback?state=made-up&code=x")
    assert page.status_code == 400 and "expired or was not started here" in page.text
    assert fake_google.requests == [], "nothing was sent to Google"
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(client.post("/api/youtube/connect").json()["url"]).query))
    assert client.get(f"/api/youtube/callback?state={q['state']}&error=access_denied").status_code == 400
    assert client.get(f"/api/youtube/callback?state={q['state']}&code=x").status_code == 400, "a state is used once"


def test_access_that_was_not_fully_granted_is_not_kept(client, client_file, fake_google, settings):
    fake_google.granted_scope = "https://www.googleapis.com/auth/youtube.readonly"
    client.post("/api/youtube/client", json={"path": str(client_file)})
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(client.post("/api/youtube/connect").json()["url"]).query))
    page = client.get(f"/api/youtube/callback?state={q['state']}&code=x")
    assert page.status_code == 400 and "not granted" in page.text
    assert not client.get("/api/youtube").json()["connected"]


def test_an_expired_token_is_renewed_and_withdrawn_access_says_so(client, client_file, fake_google, settings):
    connect(client, client_file, fake_google)
    token = yt.load_token(settings)
    token["expires_at"] = time.time() - 10
    yt.save_token(settings, token)
    http = yt.make_http()
    assert yt.access_token(settings, http) == "access-1"
    assert yt.load_token(settings)["expires_at"] > time.time() + 3000
    token = yt.load_token(settings)
    token["expires_at"] = 0
    yt.save_token(settings, token)
    fake_google.revoked = True
    with pytest.raises(yt.NotConnected, match="Connect the channel again"):
        yt.access_token(settings, http)


def test_disconnecting_revokes_and_forgets_the_token(client, client_file, fake_google, settings):
    connect(client, client_file, fake_google)
    assert not client.post("/api/youtube/disconnect").json()["connected"]
    assert fake_google.revoked and not (settings.data_dir / "youtube" / "token.json").exists()


# ---- what may be uploaded -----------------------------------------------------------------------------


def test_unverified_rights_are_refused_by_the_service_and_nothing_reaches_youtube(client, client_file, fake_google, sample_video):
    connect(client, client_file, fake_google)
    before = len(fake_google.requests)
    project, clip = finished_project(client, sample_video, rights="unverified")
    r = client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]], "override": True})
    assert r.status_code == 409 and "rights to this video are not confirmed" in r.json()["error"]
    assert len(fake_google.requests) == before and fake_google.uploads == []


def test_the_upload_stage_checks_the_rights_again_itself(client, client_file, fake_google, sample_video, settings):
    """Even a job queued some other way (or after the rights were changed) uploads nothing."""
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video, rights="licensed", creator="Studio")
    client.patch(f"/api/projects/{project['id']}/rights", json={"rights_status": "unverified"})
    client.app.state.ctx.worker.enqueue("upload", project_id=project["id"], params={"clip_ids": [clip["id"]]})
    wait_ready(client, project["id"])
    job = client.get(f"/api/jobs?project_id={project['id']}").json()[0]
    assert job["kind"] == "upload" and job["status"] == "failed" and "not confirmed" in job["error"]
    assert job["stages"][0]["attempts"] == 1, "a refusal is not retried"
    assert fake_google.uploads == []


def test_unapproved_or_unconnected_clips_are_not_uploaded(client, client_file, fake_google, sample_video):
    project, clip = finished_project(client, sample_video)
    r = client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    assert r.status_code == 409 and "No YouTube channel is connected" in r.json()["error"]
    connect(client, client_file, fake_google)
    client.patch(f"/api/projects/{project['id']}/clips/{clip['id']}", json={"review": "rejected"})
    r = client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    assert r.status_code == 409 and "Approve the clip first" in r.json()["error"]


# ---- uploading -----------------------------------------------------------------------------------------


def test_a_short_is_uploaded_as_private_with_its_text(client, client_file, fake_google, sample_video, monkeypatch):
    monkeypatch.setattr(yt, "CHUNK", 256 * 1024)
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video, rights="creator_permission", creator="Some Streamer")
    assert client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]}).status_code == 200
    wait_ready(client, project["id"])
    [sent] = fake_google.uploads
    assert sent["status"]["privacyStatus"] == "private" and sent["status"]["selfDeclaredMadeForKids"] is False
    assert sent["snippet"]["title"] == clip["meta"]["title"] and sent["snippet"]["categoryId"] == "20"
    assert sent["snippet"]["description"].startswith("Credit: Some Streamer")
    assert "Shorts" not in sent["snippet"]["tags"] and "hello" in sent["snippet"]["tags"]
    final = Path(client.app.state.ctx.settings.project_dir(project["id"]) / "final")
    assert fake_google.received == next(final.iterdir()).read_bytes(), "the rendered file, byte for byte"
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    video = clip["youtube"]
    assert video["video_id"] == "vid123" and video["privacy"] == "private" and video["channel"] == "Test Channel"
    assert video["url"] == "https://www.youtube.com/shorts/vid123" and "session" not in video
    assert not any(u.get("status", {}).get("privacyStatus") == "public" for u in fake_google.updates)

    # asking again does not upload a second copy
    client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    wait_ready(client, project["id"])
    assert len(fake_google.uploads) == 1


def test_a_dropped_upload_carries_on_where_it_stopped(client, client_file, fake_google, sample_video, monkeypatch):
    monkeypatch.setattr(yt, "CHUNK", 256 * 1024)
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video)
    fake_google.drop_after = 256 * 1024
    client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    wait_ready(client, project["id"])
    job = client.get(f"/api/jobs?project_id={project['id']}").json()[0]
    assert job["status"] == "done" and job["stages"][0]["attempts"] == 2
    assert len(fake_google.uploads) == 1, "the same upload session was resumed, not started again"
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    assert clip["youtube"]["video_id"] == "vid123"


def test_the_daily_limit_is_reported_and_not_retried(client, client_file, fake_google, sample_video):
    connect(client, client_file, fake_google)
    fake_google.quota = True
    project, clip = finished_project(client, sample_video)
    client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    project = wait_ready(client, project["id"])
    job = client.get(f"/api/jobs?project_id={project['id']}").json()[0]
    assert job["status"] == "failed" and "daily upload limit" in job["error"] and job["stages"][0]["attempts"] == 1
    assert "Not uploaded to YouTube" in project["status_detail"]


def test_angle_brackets_are_replaced_because_youtube_refuses_them():
    body = yt.video_body({"title": "a <b> c", "description": "x > y", "hashtags": ["#Shorts", "#gta"]}, "20", False)
    assert body["snippet"]["title"] == "a ‹b› c" and "›" in body["snippet"]["description"]
    assert body["snippet"]["tags"] == ["gta"] and body["status"]["containsSyntheticMedia"] is False


# ---- publishing --------------------------------------------------------------------------------------------


def uploaded(client, client_file, fake_google, sample_video, rights="own"):
    connect(client, client_file, fake_google)
    project, clip = finished_project(client, sample_video, rights=rights, creator="Studio" if rights != "own" else None)
    client.post(f"/api/projects/{project['id']}/upload", json={"clip_ids": [clip["id"]]})
    wait_ready(client, project["id"])
    [clip] = client.get(f"/api/projects/{project['id']}/clips").json()
    return project, clip


def test_publishing_needs_an_explicit_confirmation_of_the_title_shown(client, client_file, fake_google, sample_video):
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    url = f"/api/projects/{project['id']}/clips/{clip['id']}/publish"
    title = clip["youtube"]["title"]
    assert client.post(url, json={"privacy": "public", "title": title}).status_code == 409
    assert client.post(url, json={"privacy": "public", "title": "something else", "confirm": True}).status_code == 409
    assert fake_google.updates == [], "nothing changed on YouTube without a confirmation"
    r = client.post(url, json={"privacy": "public", "title": title, "confirm": True})
    assert r.status_code == 200 and r.json()["youtube"]["privacy"] == "public" and r.json()["youtube"]["published_at"]
    [update] = fake_google.updates
    assert update["status"]["privacyStatus"] == "public"
    assert update["status"]["selfDeclaredMadeForKids"] is False and update["status"]["license"] == "youtube", \
        "the rest of the video's status is kept as it was"


def test_youtube_keeping_a_video_private_is_reported_plainly(client, client_file, fake_google, sample_video):
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    fake_google.locked_private = True
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/publish",
                    json={"privacy": "public", "title": clip["youtube"]["title"], "confirm": True}).json()
    assert r["youtube"]["privacy"] == "private" and "YouTube kept this video private" in r["youtube"]["note"]
    assert r["youtube"]["published_at"] is None


def test_a_video_whose_rights_were_withdrawn_cannot_be_made_public(client, client_file, fake_google, sample_video):
    project, clip = uploaded(client, client_file, fake_google, sample_video, rights="licensed")
    client.patch(f"/api/projects/{project['id']}/rights", json={"rights_status": "unverified"})
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/publish",
                    json={"privacy": "public", "title": clip["youtube"]["title"], "confirm": True})
    assert r.status_code == 409 and "not confirmed" in r.json()["error"] and fake_google.updates == []
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/publish",
                    json={"privacy": "private", "title": clip["youtube"]["title"], "confirm": True})
    assert r.status_code == 200, "taking it back to private is always allowed"


def test_the_videos_state_can_be_read_back(client, client_file, fake_google, sample_video):
    project, clip = uploaded(client, client_file, fake_google, sample_video)
    r = client.post(f"/api/projects/{project['id']}/clips/{clip['id']}/youtube-refresh").json()
    assert r["youtube"]["upload_status"] == "processed" and r["youtube"]["privacy"] == "private"
