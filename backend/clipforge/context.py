"""Everything a request handler needs, built once per app."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .events import EventBus
from .jobs.engine import JobWorker


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    session: sessionmaker[Session]
    bus: EventBus
    worker: JobWorker


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
