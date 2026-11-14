"""Thread-safe event bus: worker threads publish, WebSocket clients receive."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500) -> None:
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        """Call from inside the event loop that will read the queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(q, None)

    def publish(self, event: dict[str, Any]) -> None:
        """Safe to call from any thread."""
        with self._lock:
            subs = list(self._subs.items())
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, event)
            except RuntimeError:  # loop already closed
                self.unsubscribe(q)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict[str, Any]) -> None:
        if q.full():  # slow client: drop the oldest event rather than block
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(event)
