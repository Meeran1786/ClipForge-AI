"""Shared fixtures.

Tests never touch the real data folder, never open a browser and never show a
file dialog: every app is built on a temporary folder, and the picker is stubbed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from clipforge import ingest
from clipforge.api import system as system_api
from clipforge.ingest import IngestError, IngestRefused, IngestSource, SourceMeta
from clipforge.ingest.ytdlp_source import YtDlpSource
from clipforge.config import REPO_ROOT, Settings
from clipforge.db import make_engine, make_session_factory
from clipforge.events import EventBus
from clipforge.jobs import engine as engine_module


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch):
    def _refuse(*_a, **_k):
        raise AssertionError("tests must not open a real file dialog")

    monkeypatch.setattr(system_api, "pick_video_file", _refuse)
    monkeypatch.setattr("webbrowser.open", lambda *_a, **_k: False)
    monkeypatch.setattr(engine_module, "RETRY_DELAY_SECONDS", 0.01)


class FakeSource(IngestSource):
    """Stands in for a platform: 'downloads' by copying a local file."""

    def __init__(self, kind: str, label: str, hosts: tuple[str, ...]) -> None:
        self.kind, self.label, self.hosts = kind, label, hosts
        self.video: Path | None = None
        self.refuse: IngestRefused | None = None
        self.transient_failures = 0
        self.metadata_calls = 0
        self.download_calls = 0
        self.est_bytes: int | None = 1_000_000

    def fetch_metadata(self, url, settings):
        self.metadata_calls += 1
        if self.refuse:
            raise self.refuse
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise IngestError("Could not fetch the video: connection reset")
        return SourceMeta(
            platform=self.kind, video_id="abc123", title="Original Stream Title",
            creator="Channel Name", creator_url="https://example.test/channel",
            duration=2.0, upload_date="2026-01-02", licence=None, webpage_url=url,
            est_bytes=self.est_bytes, max_height=360,
        )

    def download(self, url, dest_dir, settings, progress):
        self.download_calls += 1
        assert self.video is not None, "test must set a video for the fake source"
        dest_dir.mkdir(parents=True, exist_ok=True)
        progress(0.5, "halfway")
        out = dest_dir / "source.mp4"
        shutil.copyfile(self.video, out)
        progress(1.0, "done")
        return out


@pytest.fixture(autouse=True)
def fake_sources(monkeypatch) -> dict[str, FakeSource]:
    """Every test gets fake platforms; the real downloader is unreachable."""
    fakes = [
        FakeSource("youtube", "YouTube", ("youtube.com", "youtu.be")),
        FakeSource("twitch", "Twitch", ("twitch.tv",)),
        FakeSource("kick", "Kick", ("kick.com",)),
    ]
    monkeypatch.setattr(ingest, "SOURCES", fakes)

    def _no_network(*_a, **_k):
        raise AssertionError("tests must not reach the network")

    monkeypatch.setattr(YtDlpSource, "_extract", _no_network)
    return {f.kind: f for f in fakes}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ffmpeg_dir=REPO_ROOT / "tools" / "ffmpeg",
        frontend_dist=tmp_path / "no-dist",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings: Settings):
    engine = make_engine(settings.db_path)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture
def sample_video(settings: Settings, tmp_path: Path) -> Path:
    """A real two-second 640x360 clip with audio, made by the bundled FFmpeg."""
    if not settings.ffmpeg:
        pytest.skip("FFmpeg is not installed")
    out = tmp_path / "sample clip.mp4"
    subprocess.run(
        [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2",
         "-f", "lavfi", "-i", "sine=f=440:d=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return out
