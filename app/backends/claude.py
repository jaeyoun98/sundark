"""Claude Code transport, run under the user's subscription login (ADR 0002).

Input and output both use stream-json so a message can carry inline images; every agent
tool is disabled because prompts carry untrusted PDF content."""

from __future__ import annotations

import base64
import json
import os
import shutil

from ..errors import AIError, from_text
from .process import run_process, stream_process

NAME = "claude"
LABEL = "Claude Code"
MODEL_SUGGESTIONS = ["claude-haiku-5-5", "sonnet", "opus", "haiku", "default"]


def installed() -> bool:
    return shutil.which("claude") is not None


def describe() -> dict:
    return {"provider": None}


def _env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # subscription access only
    env.pop("CLAUDECODE", None)
    env["MAX_THINKING_TOKENS"] = (
        "0"  # roughly halves latency; no Task needs extended thinking
    )
    return env


def _args(model: str, system: str | None, *, stream: bool) -> list[str]:
    args = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--allowed-tools",
        "",
    ]
    if stream:
        args.append("--include-partial-messages")
    if model and model != "default":
        args += ["--model", model]
    if system:
        args += ["--append-system-prompt", system]
    return args


def _message(prompt: str, images) -> bytes:
    parts = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode(),
            },
        }
        for png in images
    ]
    parts.append({"type": "text", "text": prompt})
    msg = {"type": "user", "message": {"role": "user", "content": parts}}
    return (json.dumps(msg) + "\n").encode("utf-8")


def _result(obj: dict) -> str:
    text = obj.get("result") or ""
    if obj.get("is_error"):
        raise from_text(
            str(text) + str(obj.get("subtype", "")),
            "Claude Code could not complete the request",
        )
    return text


async def run(prompt, *, model, system=None, images=()):
    out = await run_process(
        _args(model, system, stream=False), _message(prompt, images), _env()
    )
    final = None
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "result":
            final = _result(obj)
    if final is None or not final.strip():
        raise AIError("Claude Code returned no final response")
    return final


async def stream(prompt, *, model, system=None):
    finished, emitted, final = False, False, ""
    async for raw in stream_process(
        _args(model, system, stream=True), _message(prompt, ()), _env()
    ):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("type") == "result":
            final, finished = _result(obj), True
            continue
        event = obj.get("event") or {}
        delta = event.get("delta") or {}
        if (
            obj.get("type") == "stream_event"
            and event.get("type") == "content_block_delta"
            and delta.get("type") == "text_delta"
        ):
            emitted = True
            yield delta.get("text", "")
    if not finished:
        raise AIError("Claude Code stream ended without a final result")
    if not emitted and final:
        yield final
