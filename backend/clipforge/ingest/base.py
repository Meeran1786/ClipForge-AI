"""What every video source has to provide."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..config import Settings

# progress(fraction 0..1, message). It raises when the job is cancelled or the
# app is shutting down, so sources must let its exceptions pass through.
ProgressFn = Callable[[float, str], None]

ADD_FILE_HINT = (
    " ClipForge does not sign in or work around access limits. If this is your video, "
    "download it from your own account; if it is someone else's, ask them for the file. "
    "Then add it with “Use a file from this computer”."
)


class IngestError(RuntimeError):
    """A fetch failed in a way that may work on another try (network trouble)."""


class IngestRefused(IngestError):
    """The video cannot be fetched and trying again will not help."""

    no_retry = True

    def __init__(self, reason: str, code: str = "refused", *, hint: bool = True) -> None:
        super().__init__(reason + (ADD_FILE_HINT if hint else ""))
        self.reason = reason
        self.code = code


@dataclass
class SourceMeta:
    platform: str
    video_id: str | None = None
    title: str | None = None
    creator: str | None = None
    creator_url: str | None = None
    duration: float | None = None
    upload_date: str | None = None  # YYYY-MM-DD
    licence: str | None = None
    webpage_url: str | None = None
    est_bytes: int | None = None
    max_height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IngestSource(ABC):
    kind: str  # stored as Project.source_kind
    label: str
    hosts: tuple[str, ...]

    def matches(self, url: str) -> bool:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    @abstractmethod
    def fetch_metadata(self, url: str, settings: Settings) -> SourceMeta:
        """Read the video's public details without downloading it."""

    @abstractmethod
    def download(self, url: str, dest_dir: Path, settings: Settings, progress: ProgressFn,
                 section: tuple[float, float] | None = None) -> Path:
        """Download the video (or only the part from section[0] to section[1] seconds) into dest_dir."""
