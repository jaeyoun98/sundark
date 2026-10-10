"""Hermes transport. Runs hermes_runner.py with Hermes's own Python (its dependency tree is
separate from ours). No Hermes session, memory, or profile is created or changed."""

from __future__ import annotations

import base64
import functools
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..errors import AIError, ModelUnavailableError, RateLimitError
from .process import stream_process

NAME = "hermes"
LABEL = "Hermes"
MODEL_SUGGESTIONS = [
    "gpt-6-luna",
    "gpt-6.1-sol",
    "gpt-5.6-luna",
    "gpt-6-sol",
    "gpt-6-astra",
]
RUNNER = Path(__file__).with_name("hermes_runner.py")
_ERRORS = {
    "rate_limit": RateLimitError("Subscription usage limit reached; try again later"),
    "model_unavailable": ModelUnavailableError(
        "This model is not available for your account on this AI Backend"
    ),
}


def installed() -> bool:
    return bool(os.environ.get("HERMES_PYTHON") or shutil.which("hermes"))


def _python() -> str:
    override = os.environ.get("HERMES_PYTHON")
    if override:
        return override
    exe = shutil.which("hermes")
    if not exe:
        raise AIError("Hermes is not installed; install it and run hermes setup")
    found = _interpreter(Path(exe), hops=4)
    if found:
        return found
    raise AIError(
        "Could not locate Hermes Python; set HERMES_PYTHON to its venv python"
    )


def _interpreter(path: Path, hops: int) -> str | None:
    """Python behind a hermes launcher: a python shebang, or a shell shim's `exec <target>`
    line followed to the real entry point. Never a python that merely sits next to a shim."""
    try:
        lines = (
            path.resolve().read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return None
    if lines and lines[0].startswith("#!") and "python" in lines[0]:
        return shlex.split(lines[0][2:])[0]
    for line in lines:
        words = shlex.split(line.strip()) if line.strip().startswith("exec ") else []
        if len(words) > 1 and hops > 0:
            target = Path(os.path.expandvars(words[1])).expanduser()
            if target.is_absolute():
                return _interpreter(target, hops - 1)
    return None


def _env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("HERMES_GATEWAY_", "HERMES_KANBAN_", "HERMES_PARENT_", "HERMES_SESSION")
        )
    }
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "_HERMES_GATEWAY",
        "HERMES_EPHEMERAL_SYSTEM_PROMPT",
        "HERMES_PREFILL_MESSAGES_FILE",
        "HERMES_INTERACTIVE",
        "HERMES_YOLO_MODE",
        "HERMES_ACCEPT_HOOKS",
    ):
        env.pop(name, None)
    env["HERMES_SAFE_MODE"] = "1"
    return env


def describe() -> dict:
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "config.yaml"
    stamp = home.stat().st_mtime_ns if home.exists() else 0
    return _describe(_python(), stamp)


@functools.lru_cache(maxsize=4)
def _describe(python: str, stamp: int) -> dict:
    try:
        out = subprocess.run(
            [python, str(RUNNER), "--info"],
            capture_output=True,
            env=_env(),
            timeout=30,
            check=False,
        )
        return json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise AIError("Could not read Hermes configuration; run hermes setup") from exc


async def _events(prompt, model, system, images):
    payload = {
        "prompt": prompt,
        "model": model,
        "system": system,
        "images": [base64.b64encode(png).decode() for png in images],
    }
    async for line in stream_process(
        [_python(), str(RUNNER)], json.dumps(payload).encode("utf-8"), _env()
    ):
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise AIError("Hermes returned an invalid message") from exc
        if event.get("type") == "error":
            raise _ERRORS.get(
                event.get("kind"),
                AIError("Hermes request failed; check login and model"),
            )
        yield event


async def run(prompt, *, model, system=None, images=()):
    result = None
    events = _events(prompt, model, system, images)
    try:
        async for event in events:
            if event.get("type") == "result":
                result = event.get("text", "")
    finally:
        await events.aclose()
    if not (result or "").strip():
        raise AIError("Hermes returned no final response")
    return result


async def stream(prompt, *, model, system=None):
    result, emitted = None, False
    events = _events(prompt, model, system, ())
    try:
        async for event in events:
            if event.get("type") == "delta":
                emitted = True
                yield event["text"]
            elif event.get("type") == "result":
                result = event.get("text", "")
    finally:
        await events.aclose()
    if result is None:
        raise AIError("Hermes stream ended without a final response")
    if not emitted:
        yield result
