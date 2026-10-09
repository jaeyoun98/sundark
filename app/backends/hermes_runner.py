"""Private JSON-lines bridge into the Hermes SDK. Run with Hermes's own Python.

stdin: {"prompt", "model", "system", "images": [base64 png]}
stdout: {"type": "delta"|"result"|"error", ...} lines. Never prints prompt text, PDF
content, tokens, or raw provider errors."""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
import sys

_REQUIRED = {
    "enabled_toolsets",
    "skip_context_files",
    "skip_memory",
    "load_soul_identity",
    "session_db",
    "stream_delta_callback",
}
_RATE = re.compile(r"\b429\b|rate.?limit|usage limit|too many requests|quota", re.I)
_MODEL = re.compile(
    r"model[^.]{0,80}not supported|model_not_found|invalid model|unknown model", re.I
)


def _config() -> dict:
    """The saved Hermes model settings; they win over stale gateway env vars."""
    from hermes_cli.config import load_config

    model = load_config().get("model") or {}
    if isinstance(model, str):
        model = {"default": model}
    name = (
        model.get("default")
        or model.get("model")
        or os.environ.get("HERMES_INFERENCE_MODEL")
    )
    provider = (
        model.get("provider") or os.environ.get("HERMES_INFERENCE_PROVIDER") or "auto"
    )
    if not name:
        raise RuntimeError("No Hermes model configured; run hermes setup")
    return {"model": name, "provider": provider}


def _kind(text: str) -> str:
    if _RATE.search(text or ""):
        return "rate_limit"
    if _MODEL.search(text or ""):
        return "model_unavailable"
    return "other"


def main() -> int:
    os.environ["HERMES_SAFE_MODE"] = "1"
    output = sys.stdout

    def emit(event):
        output.write(json.dumps(event, ensure_ascii=False) + "\n")
        output.flush()

    agent = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            info = _config()
            if "--info" in sys.argv[1:]:
                emit(info)
                return 0
            from hermes_cli.runtime_provider import resolve_runtime_provider
            from run_agent import AIAgent

            signature = inspect.signature(AIAgent.__init__)
            if not _REQUIRED.issubset(signature.parameters):
                raise RuntimeError("Update Hermes: text-only SDK controls are missing")
            request = json.load(sys.stdin)
            model = request.get("model") or info["model"]
            runtime = resolve_runtime_provider(
                requested=info["provider"], target_model=model
            )
            kwargs = dict(
                model=model,
                api_key=runtime.get("api_key"),
                base_url=runtime.get("base_url"),
                provider=runtime.get("provider"),
                enabled_toolsets=[],
                max_iterations=2,
                quiet_mode=True,
                save_trajectories=False,
                verbose_logging=False,
                skip_context_files=True,
                load_soul_identity=False,
                skip_memory=True,
                session_db=None,
                stream_delta_callback=lambda text, *_: (
                    text and emit({"type": "delta", "text": text})
                ),
                ephemeral_system_prompt=request.get("system")
                or "Respond with text only.",
            )
            for name in (
                "api_mode",
                "credential_pool",
                "request_overrides",
                "service_tier",
            ):
                if name in signature.parameters and runtime.get(name) is not None:
                    kwargs[name] = runtime[name]
            agent = AIAgent(**kwargs)
            if getattr(agent, "tools", None):
                raise RuntimeError("Refusing paper input: Hermes enabled tools")
            agent._persist_disabled = True
            agent._session_json_enabled = False
            images = request.get("images") or []
            message = request["prompt"]
            if images:
                message = [{"type": "text", "text": request["prompt"]}] + [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + b64},
                    }
                    for b64 in images
                ]
            result = agent.run_conversation(message)
            text = result.get("final_response") or ""
            if result.get("failed") or result.get("partial") or not text.strip():
                emit({"type": "error", "kind": _kind(str(result.get("error") or ""))})
                return 1
            emit({"type": "result", "text": text})
            return 0
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "kind": _kind(str(exc))})
        return 1
    finally:
        if agent is not None:
            with contextlib.redirect_stdout(sys.stderr):
                agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
