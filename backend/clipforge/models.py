"""Database tables."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite does not keep time zones)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    # local | youtube | twitch | kick
    source_kind: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    source_path: Mapped[str | None] = mapped_column(Text, default=None)

    creator: Mapped[str | None] = mapped_column(String(200), default=None)
    # own | licensed | creator_permission | unverified
    rights_status: Mapped[str] = mapped_column(String(30), default="unverified")
    rights_note: Mapped[str | None] = mapped_column(Text, default=None)

    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    media: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    thumbnail: Mapped[str | None] = mapped_column(Text, default=None)
    # what the platform says about the video (title, channel, licence, ...)
    source_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # 360p copy for analysis and preview, and 16 kHz mono audio for speech work
    proxy_path: Mapped[str | None] = mapped_column(Text, default=None)
    audio_path: Mapped[str | None] = mapped_column(Text, default=None)
    # analysis bookkeeping: transcript chunk plan and progress, language, model
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    # created | processing | ready | needs_rights | failed
    status: Mapped[str] = mapped_column(String(30), default="created")
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    jobs: Mapped[list[Job]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    clips: Mapped[list[Clip]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    transcript: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    moments: Mapped[list[Moment]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    # queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project | None] = relationship(back_populates="jobs")
    stages: Mapped[list[JobStage]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStage.position",
        lazy="selectin",
    )


class JobStage(Base):
    __tablename__ = "job_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    # pending | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    job: Mapped[Job] = relationship(back_populates="stages")


class Clip(Base):
    """A generated Short. Filled in from Phase 5 onwards."""

    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # candidate | rendered | approved | rejected | exported
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="clips")


class TranscriptSegment(Base):
    """One spoken line, timed on the whole video's clock."""

    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chunk: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float, index=True)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    # [{"s": start, "e": end, "w": word, "p": confidence}]
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    project: Mapped[Project] = relationship(back_populates="transcript")


class Moment(Base):
    """A stretch of the video worth turning into a Short, with its scores."""

    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    peak: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(30))
    score: Mapped[int] = mapped_column(Integer, index=True)
    # seven 0-10 sub-scores; the viral score is computed from these in code
    subscores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    visual: Mapped[str | None] = mapped_column(Text, default=None)  # what the frames showed
    visual_checked: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(20), default="ai_text")  # ai_text | signals
    rank: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="moments")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
