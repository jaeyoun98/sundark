import base64
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from app.backends import hermes_runner


def fake_sdk(calls, *, tools=None, failure=None):
    class Agent:
        def __init__(
            self,
            enabled_toolsets,
            skip_context_files,
            skip_memory,
            load_soul_identity,
            session_db,
            stream_delta_callback,
            **kw,
        ):
            calls["toolsets"] = enabled_toolsets
            calls["model"] = kw.get("model")
            self.tools = tools or []
            self.delta = stream_delta_callback

        def run_conversation(self, message, conversation_history=None):
            calls["message"] = message
            if failure:
                return {"failed": True, "error": failure}
            self.delta("답")
            return {"final_response": "답"}

        def close(self):
            calls["closed"] = True

    config = types.ModuleType("hermes_cli.config")
    config.load_config = lambda: {
        "model": {"default": "saved", "provider": "openai-codex"}
    }
    runtime = types.ModuleType("hermes_cli.runtime_provider")
    runtime.resolve_runtime_provider = lambda **kw: {"provider": "openai-codex"}
    agent = types.ModuleType("run_agent")
    agent.AIAgent = Agent
    return {
        "hermes_cli": types.ModuleType("hermes_cli"),
        "hermes_cli.config": config,
        "hermes_cli.runtime_provider": runtime,
        "run_agent": agent,
    }


def invoke(request, **kw):
    calls, out = {}, io.StringIO()
    with (
        patch.dict(sys.modules, fake_sdk(calls, **kw)),
        patch.dict(os.environ, {"HERMES_INFERENCE_MODEL": "stale"}, clear=True),
        patch.object(sys, "argv", ["runner"]),
        patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
        patch.object(sys, "stdout", out),
        patch.object(sys, "stderr", io.StringIO()),
    ):
        status = hermes_runner.main()
    return status, calls, [json.loads(line) for line in out.getvalue().splitlines()]


class RunnerTests(unittest.TestCase):
    def test_text_request_has_no_tools(self):
        status, calls, events = invoke({"prompt": "p", "model": "gpt-6-luna"})
        self.assertEqual(status, 0)
        self.assertEqual(calls["toolsets"], [])
        self.assertEqual(calls["model"], "gpt-6-luna")
        self.assertEqual([e["type"] for e in events], ["delta", "result"])

    def test_images_become_inline_content_parts(self):
        png = base64.b64encode(b"PNG").decode()
        _, calls, _ = invoke({"prompt": "read", "model": "m", "images": [png]})
        parts = calls["message"]
        self.assertEqual(parts[0], {"type": "text", "text": "read"})
        self.assertEqual(parts[1]["image_url"]["url"], "data:image/png;base64," + png)

    def test_unexpected_tool_fails_closed(self):
        status, calls, events = invoke(
            {"prompt": "p", "model": "m"}, tools=[{"name": "x"}]
        )
        self.assertEqual(status, 1)
        self.assertNotIn("message", calls)
        self.assertEqual(events[-1]["type"], "error")

    def test_failure_kind_is_classified_without_raw_text(self):
        _, _, events = invoke(
            {"prompt": "p", "model": "gpt-6.1-luna"},
            failure="The 'gpt-6.1-luna' model is not supported when using Codex",
        )
        self.assertEqual(events[-1], {"type": "error", "kind": "model_unavailable"})
        _, _, events = invoke(
            {"prompt": "p", "model": "m"}, failure="HTTP 429 rate limit"
        )
        self.assertEqual(events[-1], {"type": "error", "kind": "rate_limit"})

    def test_saved_config_beats_stale_env(self):
        calls = {}
        with (
            patch.dict(sys.modules, fake_sdk(calls)),
            patch.dict(os.environ, {"HERMES_INFERENCE_MODEL": "stale"}, clear=True),
        ):
            info = hermes_runner._config()
        self.assertEqual(info, {"model": "saved", "provider": "openai-codex"})
