"""The one interface the rest of the app uses to ask a model a question."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Protocol


class AIError(RuntimeError):
    """The model could not be reached or did not give a usable answer (worth retrying)."""


class AIUnavailable(AIError):
    """No AI provider is set up on this computer. Retrying will not help."""

    no_retry = True


class Reasoner(Protocol):
    name: str

    def ask_json(
        self,
        task: str,
        schema: dict[str, Any],
        images: list[Path] | None = None,
        check: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Give the model a task and get back a JSON object shaped like `schema`.

        `images` are shown to the model alongside the task. `check` is called
        while waiting and may raise to abandon the request (job cancelled).
        """


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Find a JSON object in a model's reply, tolerating code fences and chatter."""
    candidates = [m.strip() for m in _FENCE.findall(text)] + [text.strip()]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for start in (i for i, ch in enumerate(candidate) if ch == "{"):
            try:
                value, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None
