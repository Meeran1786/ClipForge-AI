"""Rights record: who made the video, and may it be reused.

Two rules live here, in code rather than in the interface:

* ClipForge downloads from a link only when the user has stated they own the
  video or hold permission, named the creator, and said where that permission
  comes from. (A file the user supplies themselves can always be processed.)
* Credit never changes the rights status; only permission does.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .models import Project, iso, utcnow

PUBLISHABLE = ("own", "licensed", "creator_permission")


def download_blocker(rights_status: str, creator: str | None, note: str | None) -> str | None:
    """Why ClipForge will not download this link yet, or None if it may."""
    if rights_status == "own":
        return None
    if rights_status not in PUBLISHABLE:
        return (
            "Not downloaded: confirm that you own this video or have the creator's permission. "
            "ClipForge only downloads videos you say you are allowed to use."
        )
    if not (creator or "").strip():
        return "Not downloaded: name the creator to credit."
    if not (note or "").strip():
        return (
            "Not downloaded: add where the permission comes from, for example a link to the "
            "creator's clip policy or the licence."
        )
    return None


def rights_record(project: Project) -> dict[str, Any]:
    meta = project.source_meta or {}
    return {
        "project_id": project.id,
        "title": project.title,
        "source": {
            "platform": project.source_kind,
            "url": project.source_url,
            "video_id": meta.get("video_id"),
            "original_title": meta.get("title"),
            "channel": meta.get("creator"),
            "channel_url": meta.get("creator_url"),
            "upload_date": meta.get("upload_date"),
            "platform_licence": meta.get("licence"),
        },
        "credit": project.creator,
        "rights_status": project.rights_status,
        "permission_evidence": project.rights_note,
        "may_publish": project.rights_status in PUBLISHABLE,
        "notice": (
            "Credit is not permission. A credited clip can still receive a copyright claim, "
            "and credit alone does not meet YouTube's reused-content rule for monetisation."
        ),
        "recorded_at": iso(utcnow()),
    }


def write_rights_file(settings: Settings, project: Project) -> None:
    folder = settings.project_dir(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rights.json").write_text(
        json.dumps(rights_record(project), indent=2, ensure_ascii=False), encoding="utf-8"
    )
