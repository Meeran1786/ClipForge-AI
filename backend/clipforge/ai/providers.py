"""Concrete AI providers.

`AgyReasoner`   - the Antigravity command-line tool, signed in with the owner's
                  subscription. Tested on this machine.
`GeminiReasoner` - a Gemini API key. Written to the documented API but NOT yet
                  run here: no key was available when it was built.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from .base import AIError, AIUnavailable, Reasoner, extract_json

log = logging.getLogger("clipforge.ai")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_AGY_PROMPT = (
    "Read the file task.md in the current folder and do exactly what it says. "
    "Do not run commands, browse, or change any files. Reply with the JSON only."
)


def run_cli(cmd: list[str], cwd: Path, timeout: float, check: Callable[[], None] | None) -> tuple[int, str]:
    """Run a command, polling `check` so a cancelled job stops the wait."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=out, stderr=err, creationflags=_NO_WINDOW)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if check:
                    check()
                if time.monotonic() > deadline:
                    raise AIError(f"The AI tool did not answer within {timeout:.0f} seconds")
                time.sleep(0.25)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        out.seek(0)
        return proc.returncode, out.read().decode("utf-8", "replace")


class AgyReasoner:
    """Each request runs in its own empty folder holding only the task and its images,
    in the tool's read-only planning mode, so the model has nothing else to act on."""

    def __init__(self, settings: Settings) -> None:
        exe = settings.agy_path if Path(settings.agy_path).is_file() else shutil.which("agy")
        if not exe:
            raise AIUnavailable(
                "The Antigravity command-line tool was not found. Sign in with it, or set a Gemini API key "
                "(GEMINI_API_KEY and CLIPFORGE_GEMINI_ROUTE=api in .env)."
            )
        self._exe = exe
        self._models = [m.strip() for m in settings.agy_models.split(",") if m.strip()]
        if not self._models:
            raise AIUnavailable("CLIPFORGE_AGY_MODELS is empty: name at least one model.")
        self._timeout = settings.ai_timeout_seconds
        self._tmp = settings.tmp_dir
        self._working: str | None = None  # the model that answered last time
        self.name = f"agy:{self._models[0]}"

    def ask_json(
        self,
        task: str,
        schema: dict[str, Any],
        images: list[Path] | None = None,
        check: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        self._tmp.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="ai_", dir=self._tmp))
        try:
            (work / "task.md").write_text(task, encoding="utf-8")
            (work / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
            for image in images or []:
                shutil.copyfile(image, work / image.name)

            order = ([self._working] if self._working else []) + [m for m in self._models if m != self._working]
            problems: list[str] = []
            for model in order:
                try:
                    result = self._ask(model, work, check)
                except AIError as exc:
                    problems.append(f"{model}: {exc}")
                    log.warning("AI model %s failed: %s", model, exc)
                    continue
                self._working = model
                self.name = f"agy:{model}"
                return result
            raise AIError("No AI model answered. " + " | ".join(problems))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _ask(self, model: str, work: Path, check: Callable[[], None] | None) -> dict[str, Any]:
        cmd = [
            self._exe, "-p", _AGY_PROMPT, "--mode", "plan", "--model", model,
            "--output-format", "json", "--json-schema", str(work / "schema.json"),
            "--print-timeout", f"{self._timeout}s",
        ]
        started = time.monotonic()
        code, stdout = run_cli(cmd, work, self._timeout + 30, check)
        return parse_agy_output(stdout, code, model, time.monotonic() - started)


def parse_agy_output(stdout: str, code: int, model: str = "", seconds: float = 0.0) -> dict[str, Any]:
    """Pull the answer out of the tool's JSON envelope, or explain why there is none."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = extract_json(stdout)
    if not isinstance(envelope, dict):
        raise AIError(f"unreadable reply (exit code {code})")
    usage = envelope.get("usage") or {}
    log.info("AI call %s: %s in %.0fs, %s tokens in, %s out", model, envelope.get("status"), seconds,
             usage.get("input_tokens"), usage.get("output_tokens"))
    if envelope.get("status") != "SUCCESS":
        raise AIError(str(envelope.get("error") or envelope.get("status") or "failed")[:300])
    answer = envelope.get("structured_output")
    if not isinstance(answer, dict):
        answer = extract_json(str(envelope.get("response") or ""))
    if not isinstance(answer, dict):
        raise AIError("the model returned no answer")
    return answer


class GeminiReasoner:
    """Gemini through an API key. NOT yet exercised on this machine."""

    def __init__(self, settings: Settings, api_key: str) -> None:
        if not settings.gemini_model:
            raise AIUnavailable("Set CLIPFORGE_GEMINI_MODEL in .env to the Gemini model to use.")
        from google import genai  # imported late: only needed on this route

        self._client = genai.Client(api_key=api_key)
        self._model = settings.gemini_model
        self.name = f"gemini:{self._model}"

    def ask_json(
        self,
        task: str,
        schema: dict[str, Any],
        images: list[Path] | None = None,
        check: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from google.genai import types

        parts: list[Any] = [task]
        for image in images or []:
            parts.append(f"Image file name: {image.name}")
            parts.append(types.Part.from_bytes(data=image.read_bytes(), mime_type="image/jpeg"))
        if check:
            check()
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_json_schema=schema
                ),
            )
        except Exception as exc:  # the SDK raises many types; all mean "no answer"
            raise AIError(f"Gemini request failed: {str(exc)[:300]}") from exc
        answer = extract_json(response.text or "")
        if answer is None:
            raise AIError("Gemini returned no JSON")
        return answer


def _api_key(settings: Settings) -> str | None:
    """GEMINI_API_KEY from the environment or the project's .env (it has no CLIPFORGE_ prefix)."""
    import os

    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key.strip() or None
    env_file = settings.model_config.get("env_file")
    if env_file and Path(str(env_file)).is_file():
        for line in Path(str(env_file)).read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GEMINI_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    return None


def make_reasoner(settings: Settings) -> Reasoner:
    if settings.gemini_route == "api":
        key = _api_key(settings)
        if not key:
            raise AIUnavailable("CLIPFORGE_GEMINI_ROUTE is 'api' but GEMINI_API_KEY is not set in .env.")
        return GeminiReasoner(settings, key)
    return AgyReasoner(settings)
