"""Sources fetched with yt-dlp: public, non-DRM video only.

Deliberately absent from the options below: cookies, browser cookie import,
usernames and passwords, geo-bypass, and browser impersonation. If a platform
asks for a sign-in or blocks the request, the fetch stops with an explanation
instead of working around it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from .base import IngestError, IngestRefused, IngestSource, ProgressFn, SourceMeta

log = logging.getLogger("clipforge.ingest")

# Resolution first (up to 1080p: a vertical Short is cut from it), then H.264
# and AAC when the platform offers them at that size.
FORMAT = "bv*+ba/b"
FORMAT_SORT = ["res:1080", "vcodec:h264", "acodec:aac"]

# (needles, code, message). First match wins, so specific rules come first.
_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("drm",), "drm", "This video is DRM-protected, and ClipForge does not remove copy protection."),
    (("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"),
     "age", "This video is age-restricted, so {platform} only shows it to signed-in viewers."),
    (("not a bot", "captcha"), "bot_check",
     "{platform} asked for a sign-in to prove this is not automated access."),
    (("private video", "video is private", "is private"), "private", "This video is private."),
    (("members-only", "members only", "join this channel", "subscriber-only", "subscribers only",
      "only available to subscribers", "must be subscribed", "sub-only"),
     "members", "This video is for members or subscribers only."),
    (("not available in your country", "geo-restricted", "geo restricted", "blocked in your country",
      "not available from your location", "not made this video available in your country"),
     "geo", "This video is not available in your country."),
    (("live event will begin", "premieres in", "will begin in"), "upcoming", "This video has not started yet."),
    (("login required", "log in to", "sign in to", "requires authentication", "authentication required",
      "use --cookies"), "login", "This video needs a sign-in."),
    (("youtube:tab", "videos tab", "is a playlist", "is a channel"), "playlist",
     "This link is a playlist or channel, not one video."),
    (("video unavailable", "is unavailable", "has been removed", "does not exist", "no longer available",
      "has been deleted", "not_found", "resource not found", "http error 404", "http error 410"),
     "gone", "This video is unavailable or has been removed."),
    (("http error 403", "forbidden", "cloudflare", "http error 429", "too many requests"),
     "blocked", "{platform} blocked automated access to this video."),
    (("unsupported url", "no video formats found", "requested format is not available"),
     "unsupported", "ClipForge could not find a downloadable video at this link."),
]


def classify_error(message: str, platform_label: str) -> IngestError:
    """Turn a yt-dlp error message into a refusal (final) or a retryable error."""
    text = message.lower()
    for needles, code, template in _RULES:
        if any(n in text for n in needles):
            return IngestRefused(template.format(platform=platform_label), code)
    clean = message.replace("ERROR: ", "").strip()
    return IngestError(f"Could not fetch the video: {clean[:300]}")


class _Quiet:
    """Send yt-dlp's chatter to our log instead of the console."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp error: %s", msg)


def _size_of(fmt: dict[str, Any], duration: float | None = None) -> int:
    known = fmt.get("filesize") or fmt.get("filesize_approx")
    if known:
        return int(known)
    # Stream recordings (HLS) carry no size: estimate from bitrate x length.
    if fmt.get("tbr") and duration:
        return int(float(fmt["tbr"]) * 1000 / 8 * duration)
    return 0


def _date(raw: str | None) -> str | None:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return None


class YtDlpSource(IngestSource):
    def _options(self, settings: Settings, **extra: Any) -> dict[str, Any]:
        cache = settings.data_dir / "cache" / "yt-dlp"
        cache.mkdir(parents=True, exist_ok=True)
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "logger": _Quiet(),
            "format": FORMAT,
            "format_sort": FORMAT_SORT,
            "cachedir": str(cache),
            "geo_bypass": False,
            "retries": 3,
            "fragment_retries": 5,
            "socket_timeout": 30,
        }
        if settings.ffmpeg:
            opts["ffmpeg_location"] = str(Path(settings.ffmpeg).parent)
        opts.update(extra)
        return opts

    def _extract(self, url: str, opts: dict[str, Any], *, download: bool) -> dict[str, Any]:
        import yt_dlp  # imported here: it is slow to load and only needed for links

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as exc:
            raise classify_error(str(exc), self.label) from exc
        if not isinstance(info, dict):
            raise IngestRefused("ClipForge could not find a video at this link.", "unsupported")
        return info

    def _check_playable(self, info: dict[str, Any]) -> None:
        if info.get("_type") == "playlist" or info.get("entries") is not None:
            raise IngestRefused(
                "This link is a playlist or channel. Paste the link to one video.", "playlist", hint=False
            )
        status = info.get("live_status")
        if info.get("is_live") or status == "is_live":
            raise IngestRefused(
                "This stream is still live. Use the link to its recording once the stream has ended.",
                "live", hint=False,
            )
        if status == "is_upcoming":
            raise IngestRefused("This video has not started yet.", "upcoming", hint=False)
        if status == "post_live":
            raise IngestRefused(
                f"{self.label} is still processing this stream's recording. Try again later.",
                "processing", hint=False,
            )

    def fetch_metadata(self, url: str, settings: Settings) -> SourceMeta:
        info = self._extract(url, self._options(settings, skip_download=True), download=False)
        self._check_playable(info)
        chosen = info.get("requested_formats") or [info]
        est = sum(_size_of(f, info.get("duration")) for f in chosen) or None
        heights = [f.get("height") or 0 for f in chosen] or [0]
        return SourceMeta(
            platform=self.kind,
            video_id=str(info["id"]) if info.get("id") is not None else None,
            title=info.get("title"),
            creator=info.get("channel") or info.get("uploader"),
            creator_url=info.get("channel_url") or info.get("uploader_url"),
            duration=float(info["duration"]) if info.get("duration") else None,
            upload_date=_date(info.get("upload_date")),
            licence=info.get("license"),
            webpage_url=info.get("webpage_url") or url,
            est_bytes=est,
            max_height=max(heights) or None,
        )

    def download(self, url: str, dest_dir: Path, settings: Settings, progress: ProgressFn,
                 section: tuple[float, float] | None = None) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        state = {"finished_bytes": 0, "total": 0}

        def hook(d: dict[str, Any]) -> None:
            if d.get("status") == "downloading":
                done = int(d.get("downloaded_bytes") or 0)
                part_total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                so_far = state["finished_bytes"] + done
                total = max(state["total"], state["finished_bytes"] + part_total, 1)
                speed = d.get("speed")
                eta = d.get("eta")
                bits = [f"{so_far / 1e6:.0f} of {total / 1e6:.0f} MB"]
                if speed:
                    bits.append(f"{speed / 1e6:.1f} MB/s")
                if eta is not None:
                    bits.append(f"{int(eta) // 60}:{int(eta) % 60:02d} left")
                progress(min(0.97, so_far / total), " · ".join(bits))
            elif d.get("status") == "finished":
                state["finished_bytes"] += int(d.get("total_bytes") or d.get("downloaded_bytes") or 0)
                progress(min(0.97, state["finished_bytes"] / max(state["total"], state["finished_bytes"], 1)),
                         "Joining video and audio")

        opts = self._options(
            settings,
            outtmpl=str(dest_dir / "source.%(ext)s"),
            merge_output_format="mp4",
            progress_hooks=[hook],
            concurrent_fragment_downloads=4,
            continuedl=True,
            overwrites=False,
        )
        if section:
            from yt_dlp.utils import download_range_func

            opts["download_ranges"] = download_range_func(None, [section])
        # Size the progress bar from the formats that will actually be fetched.
        probe = self._extract(url, self._options(settings, skip_download=True), download=False)
        self._check_playable(probe)
        state["total"] = sum(
            _size_of(f, probe.get("duration")) for f in (probe.get("requested_formats") or [probe])
        )
        if section and probe.get("duration"):
            state["total"] = int(state["total"] * min(1.0, (section[1] - section[0]) / float(probe["duration"])))

        self._extract(url, opts, download=True)

        files = [
            p for p in dest_dir.glob("source.*")
            if p.suffix.lower() not in (".part", ".ytdl", ".tmp") and ".f" not in p.stem[len("source"):]
        ]
        if not files:
            raise IngestError("The download finished but no video file was produced")
        result = max(files, key=lambda p: p.stat().st_size)
        progress(1.0, f"Downloaded {result.stat().st_size / 1e6:.0f} MB")
        return result


class YouTubeSource(YtDlpSource):
    kind = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtu.be")


class TwitchSource(YtDlpSource):
    kind = "twitch"
    label = "Twitch"
    hosts = ("twitch.tv",)


class KickSource(YtDlpSource):
    """Kick's official API has no endpoints for recordings, so this relies on the
    same unofficial lookup the downloader uses. When Kick does not answer it,
    say so plainly instead of claiming the video was removed."""

    kind = "kick"
    label = "Kick"
    hosts = ("kick.com",)

    def _extract(self, url: str, opts: dict[str, Any], *, download: bool) -> dict[str, Any]:
        try:
            return super()._extract(url, opts, download=download)
        except IngestRefused as exc:
            if exc.code not in ("gone", "blocked"):
                raise
            raise IngestRefused(
                "Kick did not give ClipForge this recording. Kick has no official way to download "
                "recordings, and the unofficial route does not always work.",
                "kick_unavailable",
            ) from exc
