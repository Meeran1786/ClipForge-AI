"""Application settings. Values come from CLIPFORGE_* environment variables or .env."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_",
        env_file=str(REPO_ROOT / ".env"),
        extra="ignore",
    )

    data_dir: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    gemini_route: str = "agy"
    ffmpeg_dir: Path = REPO_ROOT / "tools" / "ffmpeg"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipforge.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def project_dir(self, project_id: int) -> Path:
        return self.projects_dir / str(project_id)

    def _tool(self, name: str) -> str | None:
        bundled = self.ffmpeg_dir / f"{name}.exe"
        if bundled.is_file():
            return str(bundled)
        return shutil.which(name)

    @property
    def ffmpeg(self) -> str | None:
        return self._tool("ffmpeg")

    @property
    def ffprobe(self) -> str | None:
        return self._tool("ffprobe")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.logs_dir, self.projects_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
