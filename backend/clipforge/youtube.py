"""Publishing to the owner's YouTube channel through the YouTube Data API.

How it works:

* The owner creates an OAuth client ("Desktop app") in their own Google Cloud
  project and gives ClipForge its JSON file. ClipForge never sees a password:
  the owner signs in on Google's own page in their browser, Google sends a
  one-time code back to this app on 127.0.0.1, and the app exchanges it for a
  token (with PKCE). The token is kept in the data folder, never in git.
* A Short is uploaded as **private**, with resumable chunks so a dropped
  connection or a restart carries on where it stopped.
* Making it public (or unlisted) is a separate step that needs the owner's
  explicit confirmation for that clip.
* Nothing from a video whose rights are not confirmed is ever uploaded or
  published: `publish_blocker` refuses it, in the API and again right before
  any request to YouTube.

YouTube's own rules that matter here: uploads cost 1,600 of the default 10,000
quota units a day (about six Shorts); and videos uploaded through an API project
Google has not audited are kept private by YouTube, whatever the app asks for.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .rights import PUBLISHABLE

SCOPES = ["https://www.googleapis.com/auth/youtube"]  # upload, and change a video's privacy afterwards
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024  # a multiple of 256 KiB, as resumable uploads require

CATEGORIES = {"20": "Gaming", "24": "Entertainment", "23": "Comedy", "22": "People & Blogs", "17": "Sports",
              "1": "Film & Animation", "10": "Music", "28": "Science & Technology", "27": "Education"}
PRIVACY = ("private", "unlisted", "public")


class YouTubeError(RuntimeError):
    """Something went wrong talking to YouTube (worth retrying)."""


class Refused(YouTubeError):
    """Will not be done: retrying cannot help."""

    no_retry = True


class NotConnected(Refused):
    pass


def make_http() -> httpx.Client:
    """The HTTP client for Google. Tests replace this with a stand-in."""
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=False)


# ---- the owner's OAuth client and token -------------------------------------------------------


@dataclass
class ClientInfo:
    client_id: str
    client_secret: str
    project_id: str | None = None


def read_client(path: Path) -> ClientInfo:
    """The OAuth client from the JSON file Google Cloud gives for a Desktop app."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"That file could not be read as the client file from Google Cloud ({exc}).") from exc
    if "web" in data and "installed" not in data:
        raise Refused("This is a client for a web application. Create one of type “Desktop app” and use its file.")
    info = data.get("installed") or {}
    if not info.get("client_id") or not info.get("client_secret"):
        raise Refused("This is not an OAuth client file. In Google Cloud, create an OAuth client ID of type "
                      "“Desktop app” and download its JSON.")
    return ClientInfo(info["client_id"], info["client_secret"], info.get("project_id"))


def client_file(settings) -> Path:
    return settings.youtube_dir / "client.json"


def token_file(settings) -> Path:
    return settings.youtube_dir / "token.json"


def save_client(settings, source: Path) -> ClientInfo:
    info = read_client(source)
    settings.youtube_dir.mkdir(parents=True, exist_ok=True)
    _write_private(client_file(settings), Path(source).read_text(encoding="utf-8"))
    token_file(settings).unlink(missing_ok=True)  # a token belongs to the client that issued it
    return info


def _write_private(path: Path, text: str) -> None:
    part = path.with_name(path.name + ".part")
    part.write_text(text, encoding="utf-8")
    os.replace(part, path)


def load_token(settings) -> dict[str, Any] | None:
    path = token_file(settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_token(settings, token: dict[str, Any]) -> None:
    settings.youtube_dir.mkdir(parents=True, exist_ok=True)
    _write_private(token_file(settings), json.dumps(token, indent=2))


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def auth_url(client: ClientInfo, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "client_id": client.client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "state": state, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(query)


def _google_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    err = data.get("error")
    if isinstance(err, dict):
        reasons = [e.get("reason") for e in err.get("errors") or [] if e.get("reason")]
        return (err.get("message") or "") + (f" ({', '.join(reasons)})" if reasons else "")
    return str(data.get("error_description") or err or f"HTTP {resp.status_code}")


def _reasons(resp: httpx.Response) -> set[str]:
    try:
        err = resp.json().get("error")
    except ValueError:
        return set()
    if isinstance(err, dict):
        return {e.get("reason") for e in err.get("errors") or [] if e.get("reason")}
    return {str(err)} if err else set()


def exchange_code(http: httpx.Client, client: ClientInfo, code: str, verifier: str, redirect_uri: str) -> dict:
    resp = http.post(TOKEN_URL, data={
        "code": code, "client_id": client.client_id, "client_secret": client.client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    if resp.status_code != 200:
        raise Refused(f"Google did not accept the sign-in: {_google_error(resp)}")
    data = resp.json()
    if not data.get("refresh_token"):
        raise Refused("Google gave no lasting access. Remove ClipForge's access in your Google account and connect again.")
    granted = set((data.get("scope") or "").split())
    if not set(SCOPES) <= granted:
        raise Refused("Access to manage your YouTube videos was not granted. Connect again and allow it.")
    return {"refresh_token": data["refresh_token"], "access_token": data["access_token"],
            "expires_at": time.time() + float(data.get("expires_in", 3600)), "scope": data.get("scope")}


def access_token(settings, http: httpx.Client) -> str:
    """A valid access token, refreshed when it is about to run out."""
    token = load_token(settings)
    if not token or not token.get("refresh_token"):
        raise NotConnected("No YouTube channel is connected. Connect one in Settings.")
    if token.get("access_token") and float(token.get("expires_at", 0)) > time.time() + 60:
        return token["access_token"]
    client = read_client(client_file(settings))
    resp = http.post(TOKEN_URL, data={"client_id": client.client_id, "client_secret": client.client_secret,
                                      "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    if resp.status_code in (400, 401) and "invalid_grant" in resp.text:
        raise NotConnected("YouTube access was withdrawn or has expired. Connect the channel again in Settings.")
    if resp.status_code != 200:
        raise YouTubeError(f"Could not renew access to YouTube: {_google_error(resp)}")
    data = resp.json()
    token.update(access_token=data["access_token"], expires_at=time.time() + float(data.get("expires_in", 3600)))
    save_token(settings, token)
    return token["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fetch_channel(http: httpx.Client, token: str) -> dict[str, str]:
    resp = http.get(f"{API}/channels", params={"part": "snippet", "mine": "true"}, headers=_auth(token))
    if resp.status_code != 200:
        raise YouTubeError(f"Could not read your channel: {_google_error(resp)}")
    items = resp.json().get("items") or []
    if not items:
        raise Refused("This Google account has no YouTube channel. Create one on YouTube first.")
    ch = items[0]
    return {"id": ch["id"], "title": ch["snippet"]["title"], "url": f"https://www.youtube.com/channel/{ch['id']}"}


def revoke(settings, http: httpx.Client) -> None:
    token = load_token(settings)
    if token and token.get("refresh_token"):
        try:
            http.post(REVOKE_URL, data={"token": token["refresh_token"]})
        except httpx.HTTPError:
            pass  # forgetting it here is what matters; the owner can also remove access in their Google account
    token_file(settings).unlink(missing_ok=True)


# ---- what may be published ---------------------------------------------------------------------


def publish_blocker(rights_status: str, clip, override: bool = False) -> str | None:
    """Why this clip may not go to YouTube, or None. Checked in the API and again before every request."""
    if rights_status not in PUBLISHABLE:
        return ("Refused: the rights to this video are not confirmed. Record that you own it, or the creator's "
                "permission with its evidence, before anything is uploaded.")
    if clip.review != "approved":
        return "Only approved clips are uploaded."
    final = clip.final or {}
    if not final.get("file") or not Path(final["file"]).is_file():
        return "Render the final Short first."
    if not clip.meta or not (clip.meta.get("title") or "").strip():
        return "The Short has no title yet."
    qc = final.get("qc") or {}
    if qc and not qc.get("passed", True) and not override:
        return "It failed its quality checks. Fix it, or upload it anyway."
    return None


# ---- uploading -----------------------------------------------------------------------------------


def _clean(text: str) -> str:
    return text.replace("<", "‹").replace(">", "›")  # YouTube refuses angle brackets in titles and descriptions


def video_body(meta: dict[str, Any], category_id: str, made_for_kids: bool) -> dict[str, Any]:
    tags = [t.lstrip("#") for t in meta.get("hashtags") or [] if t.lstrip("#").lower() != "shorts"]
    return {
        "snippet": {"title": _clean(meta["title"])[:100], "description": _clean(meta.get("description") or "")[:5000],
                    "tags": tags[:15], "categoryId": str(category_id)},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": bool(made_for_kids),
                   "containsSyntheticMedia": False},
    }


def _next_offset(resp: httpx.Response) -> int:
    rng = resp.headers.get("Range")  # "bytes=0-1234": the server has bytes up to 1234
    return int(rng.split("-")[-1]) + 1 if rng else 0


def upload(
    settings, http: httpx.Client, file: Path, body: dict[str, Any], *, session: str | None = None,
    on_session: Callable[[str], None] | None = None, on_progress: Callable[[float], None] | None = None,
    check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Upload a video file as described by `body`, resuming `session` when one is given. Returns the video."""
    size = file.stat().st_size
    token = access_token(settings, http)
    offset = 0
    if session:
        resp = http.put(session, headers={**_auth(token), "Content-Range": f"bytes */{size}"})
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 308:
            offset = _next_offset(resp)
        else:
            session = None  # expired or unknown: start again
    if not session:
        resp = http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={**_auth(token), "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(size)}, json=body)
        if resp.status_code != 200 or not resp.headers.get("Location"):
            reasons = _reasons(resp)
            if reasons & {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded"}:
                raise Refused(f"YouTube's daily upload limit is reached ({', '.join(sorted(reasons))}). "
                              "Try again tomorrow.")
            if resp.status_code in (400, 403):
                raise Refused(f"YouTube refused the upload: {_google_error(resp)}")
            raise YouTubeError(f"YouTube did not start the upload: {_google_error(resp)}")
        session = resp.headers["Location"]
        offset = 0
        if on_session:
            on_session(session)
    with file.open("rb") as fh:
        while True:
            if check:
                check()
            fh.seek(offset)
            chunk = fh.read(CHUNK)
            end = offset + len(chunk) - 1
            resp = http.put(session, content=chunk, headers={
                **_auth(token), "Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"})
            if resp.status_code in (200, 201):
                if on_progress:
                    on_progress(1.0)
                return resp.json()
            if resp.status_code == 308:
                offset = _next_offset(resp)
                if on_progress:
                    on_progress(offset / size)
                continue
            if resp.status_code == 401:
                token = access_token(settings, http)
                continue
            raise YouTubeError(f"The upload stopped at {offset / size:.0%}: {_google_error(resp)}")


def video_status(settings, http: httpx.Client, video_id: str) -> dict[str, Any] | None:
    token = access_token(settings, http)
    resp = http.get(f"{API}/videos", params={"part": "status,processingDetails", "id": video_id},
                    headers=_auth(token))
    if resp.status_code != 200:
        raise YouTubeError(f"Could not read the video's status: {_google_error(resp)}")
    items = resp.json().get("items") or []
    return items[0] if items else None


def set_privacy(settings, http: httpx.Client, video_id: str, privacy: str) -> dict[str, Any]:
    """Change a video's privacy, keeping the rest of its status as it is. Returns the status YouTube reports."""
    if privacy not in PRIVACY:
        raise Refused(f"Unknown privacy: {privacy}")
    current = video_status(settings, http, video_id)
    if current is None:
        raise Refused("That video is no longer on your channel.")
    keep = {k: v for k, v in (current.get("status") or {}).items()
            if k in ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")}
    token = access_token(settings, http)
    resp = http.put(f"{API}/videos", params={"part": "status"}, headers=_auth(token),
                    json={"id": video_id, "status": {**keep, "privacyStatus": privacy}})
    if resp.status_code != 200:
        if resp.status_code in (400, 403):
            raise Refused(f"YouTube refused the change: {_google_error(resp)}")
        raise YouTubeError(f"Could not change the video: {_google_error(resp)}")
    return resp.json().get("status") or {}


def shorts_url(video_id: str) -> str:
    return f"https://www.youtube.com/shorts/{video_id}"


def studio_url(video_id: str) -> str:
    return f"https://studio.youtube.com/video/{video_id}/edit"
