"""Request models and serialisers shared by the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .duration import MAX_TARGET, MIN_TARGET, DurationSettings
from .models import Project, iso
from .rights import download_blocker

CATEGORIES = [
    "funny", "shocking", "unexpected", "horror", "rage",
    "exciting", "emotional", "failure", "insane",
]

RightsStatus = Literal["own", "licensed", "creator_permission", "unverified"]


class ProjectOptions(BaseModel):
    """What to look for and how many clips to make. Also the saved defaults."""

    model_config = ConfigDict(extra="ignore")  # older projects carry min_duration / max_duration

    categories: list[str] = Field(default_factory=lambda: list(CATEGORIES))
    max_clips: int = Field(default=8, ge=1, le=50)
    min_score: int = Field(default=60, ge=0, le=100)

    # Smart Duration: the target guides the cut, it never forces one.
    target_duration: int = Field(default=45, ge=MIN_TARGET, le=MAX_TARGET)
    duration_mode: Literal["strict", "smart", "flexible"] = "smart"
    tolerance_pct: int = Field(default=10, ge=0, le=30)  # Smart mode only
    split_parts: bool = False
    max_parts: int = Field(default=3, ge=2, le=5)
    context: Literal["tight", "balanced", "full"] = "balanced"
    trim_silence: bool = True

    @model_validator(mode="before")
    @classmethod
    def _from_old_length_pair(cls, data: Any) -> Any:
        """Projects made before Smart Duration stored a shortest/longest pair."""
        if isinstance(data, dict) and "target_duration" not in data and "max_duration" in data:
            try:
                longest = int(data["max_duration"])
            except (TypeError, ValueError):
                return data
            data = {**data, "target_duration": max(MIN_TARGET, min(MAX_TARGET, longest))}
        return data

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, value: list[str]) -> list[str]:
        unknown = [c for c in value if c not in CATEGORIES]
        if unknown:
            raise ValueError(f"Unknown categories: {', '.join(unknown)}")
        if not value:
            raise ValueError("Pick at least one category")
        return [c for c in CATEGORIES if c in value]  # stable order, no duplicates

    def duration_settings(self) -> DurationSettings:
        return DurationSettings(
            target=float(self.target_duration), mode=self.duration_mode, tolerance=self.tolerance_pct / 100,
            split=self.split_parts, max_parts=self.max_parts, context=self.context,
            trim_silence=self.trim_silence,
        )


def normalise_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Stored options in today's shape, whatever version of the app saved them."""
    try:
        return ProjectOptions(**(raw or {})).model_dump()
    except ValueError:
        return ProjectOptions().model_dump()


class ProjectCreate(BaseModel):
    source: Literal["local", "url"]
    path: str | None = None
    url: str | None = None
    title: str | None = Field(default=None, max_length=300)
    creator: str | None = Field(default=None, max_length=200)
    rights_status: RightsStatus = "unverified"
    rights_note: str | None = Field(default=None, max_length=2000)
    options: ProjectOptions = Field(default_factory=ProjectOptions)


class RightsUpdate(BaseModel):
    rights_status: RightsStatus
    creator: str | None = Field(default=None, max_length=200)
    rights_note: str | None = Field(default=None, max_length=2000)


class AttachFile(BaseModel):
    path: str


class ClipReview(BaseModel):
    review: Literal["pending", "approved", "rejected"]
    override: bool = False  # approve a clip even though it failed a context check


def project_to_dict(p: Project, *, clip_count: int = 0) -> dict[str, Any]:
    is_link = p.source_kind != "local"
    analysis = dict(p.analysis or {})
    transcript = dict(analysis.get("transcript") or {})
    transcript.pop("plan", None)  # internal bookkeeping
    return {
        "analysis": {
            "audio": analysis.get("audio"),
            "video": analysis.get("video"),
            "transcript": transcript or None,
            "moments": analysis.get("moments"),
            "clips": analysis.get("clips"),
        },
        "source_meta": p.source_meta,
        "has_proxy": bool(p.proxy_path),
        "has_audio_file": bool(p.audio_path),
        # for a link that has not been downloaded: why not (None = allowed)
        "download_blocker": (
            download_blocker(p.rights_status, p.creator, p.rights_note)
            if is_link and not p.source_path
            else None
        ),
        "id": p.id,
        "title": p.title,
        "source_kind": p.source_kind,
        "source_url": p.source_url,
        "source_path": p.source_path,
        "creator": p.creator,
        "rights_status": p.rights_status,
        "rights_note": p.rights_note,
        "options": normalise_options(p.options),
        "media": p.media,
        "has_thumbnail": bool(p.thumbnail),
        "status": p.status,
        "status_detail": p.status_detail,
        "clip_count": clip_count,
        "created_at": iso(p.created_at),
        "updated_at": iso(p.updated_at),
    }
