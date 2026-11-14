"""Video sources. Add a platform by writing an IngestSource and listing it here."""

from __future__ import annotations

from .base import IngestError, IngestRefused, IngestSource, SourceMeta
from .ytdlp_source import KickSource, TwitchSource, YouTubeSource

SOURCES: list[IngestSource] = [YouTubeSource(), TwitchSource(), KickSource()]

PLATFORM_LABELS = {s.kind: s.label for s in SOURCES} | {"local": "Local file"}


def source_for(url: str) -> IngestSource | None:
    return next((s for s in SOURCES if s.matches(url)), None)


def source_by_kind(kind: str) -> IngestSource | None:
    return next((s for s in SOURCES if s.kind == kind), None)


def detect_platform(url: str) -> str | None:
    source = source_for(url)
    return source.kind if source else None


__all__ = [
    "SOURCES",
    "PLATFORM_LABELS",
    "IngestError",
    "IngestRefused",
    "IngestSource",
    "SourceMeta",
    "detect_platform",
    "source_by_kind",
    "source_for",
]
