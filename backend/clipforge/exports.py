"""Exporting finished Shorts: the video, its text and its rights record, side by side in one folder.

For each Short three files are written into the project's export folder:

* `NN_title.mp4`   — the finished 1080×1920 video, copied as rendered;
* `NN_title.txt`   — title, description, hashtags and pinned comment, ready to paste;
* `NN_title.json`  — the same text plus the rights record, the music licence, the
  quality report and where in the original video the clip comes from.

A copy of the project's `rights.json` sits beside them. A Short whose rights are
not confirmed is exported with that stated in both sidecars; publishing such a
Short is refused in Phase 10.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .models import Clip, Project, iso, utcnow
from .rights import PUBLISHABLE, rights_record


def safe_name(text: str, limit: int = 60) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in text).strip()
    return "_".join(cleaned.split())[:limit].strip("_-") or "clip"


def export_folder(exports_dir: Path, project: Project) -> Path:
    return exports_dir / f"{project.id:03d}_{safe_name(project.title or 'project', 40)}"


def blocker(clip: Clip, override: bool = False) -> str | None:
    """Why this clip cannot be exported yet, or None."""
    final = clip.final or {}
    if clip.review != "approved":
        return "Only approved clips are exported."
    if not final.get("file") or not Path(final["file"]).is_file():
        return "Render the final Short first."
    if not clip.meta:
        return "The title and description have not been written yet. Render the final Short again."
    qc = final.get("qc") or {}
    if not qc.get("passed", final.get("passed")) and not override:
        bad = [c["label"] for c in qc.get("checks") or [] if c.get("status") == "fail"]
        return ("It failed its quality checks" + (f" ({', '.join(bad)})" if bad else "")
                + ". Fix it, or export it anyway.")
    return None


def paste_text(meta: dict[str, Any]) -> str:
    return "\n".join([
        "TITLE", meta["title"], "",
        "DESCRIPTION", meta["description"], "",
        "HASHTAGS", " ".join(meta.get("hashtags") or []), "",
        "PINNED COMMENT", meta.get("pinned_comment") or "", "",
    ])


def _copy(src: Path, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dest)  # only a complete copy ever has the final name


def export_clip(exports_dir: Path, project: Project, clip: Clip, override: bool = False) -> dict[str, Any]:
    """Write the clip's three files. Raises ValueError with the reason when it may not be exported."""
    why = blocker(clip, override)
    if why:
        raise ValueError(why)
    folder = export_folder(exports_dir, project)
    folder.mkdir(parents=True, exist_ok=True)
    meta = dict(clip.meta)
    final = dict(clip.final)
    prefix = f"{clip.rank or 0:02d}_" + (f"part{clip.part}of{clip.parts}_" if (clip.parts or 1) > 1 else "")
    title = re.sub(r"\s*\(Part \d+/\d+\)\s*$", "", meta.get("title") or "clip")  # the prefix already says which part
    stem = prefix + safe_name(title, 50)
    video = folder / f"{stem}.mp4"
    for old in folder.iterdir():  # an earlier export of this clip under an older title
        same_clip = old.name.startswith(prefix) and ((clip.parts or 1) > 1 or not old.name[len(prefix):].startswith("part"))
        if same_clip and old.stem != stem and old.suffix in (".mp4", ".txt", ".json"):
            old.unlink(missing_ok=True)
    _copy(Path(final["file"]), video)

    rights = rights_record(project)
    may_publish = project.rights_status in PUBLISHABLE
    qc = final.get("qc") or {}
    record = {
        "title": meta["title"], "description": meta["description"], "hashtags": meta.get("hashtags") or [],
        "pinned_comment": meta.get("pinned_comment"), "text_written_by": meta.get("by"),
        "edited_by_owner": bool(meta.get("edited")),
        "video": video.name,
        "clip": {"rank": clip.rank, "part": clip.part, "parts": clip.parts, "category": clip.category,
                 "viral_score": clip.score, "duration": final.get("duration"),
                 "from_original": [[round(a, 2), round(b, 2)] for a, b in (clip.segments or [])]},
        "render": {k: final.get(k) for k in ("width", "height", "fps", "encoder", "video_codec", "audio_codec",
                                             "layout", "loudness", "rendered_at")},
        "quality": {"score": qc.get("score"), "passed": qc.get("passed"), "exported_anyway": bool(
            override and not qc.get("passed", True)), "checks": qc.get("checks")},
        "music": ({k: (clip.music or {}).get(k) for k in ("title", "licence_name", "source", "credit")}
                  if (clip.music or {}).get("used") else None),
        "rights": rights,
        "may_publish": may_publish,
        "warning": None if may_publish else (
            "The rights to the source video are not confirmed. Do not publish this Short until you have recorded "
            "that you own the video or have the creator's permission."),
        "exported_at": iso(utcnow()),
    }
    text = paste_text(meta)
    if not may_publish:
        text = "WARNING: rights not confirmed. Do not publish.\n\n" + text
    (folder / f"{stem}.txt").write_text(text, encoding="utf-8")
    (folder / f"{stem}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "rights.json").write_text(json.dumps(rights, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"folder": str(folder), "video": video.name, "files": [video.name, f"{stem}.txt", f"{stem}.json"],
            "exported_at": record["exported_at"], "may_publish": may_publish}
