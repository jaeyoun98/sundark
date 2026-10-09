import base64
import json
import os
import unittest
from unittest.mock import patch

from app.backends import claude
from app.errors import AIError, RateLimitError


def result_line(text="ok", is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": text}) + "\n"


def delta_line(text):
    event = {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": text},
    }
    return json.dumps({"type": "stream_event", "event": event}) + "\n"


class ClaudeBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_disables_tools_strips_api_key_and_sends_images_inline(self):
        seen = {}

        async def fake(args, stdin, env, timeout=300):
            seen.update(args=args, stdin=stdin, env=env)
            return result_line("E=mc^2").encode()

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("app.backends.claude.run_process", new=fake),
        ):
            text = await claude.run(
                "read it", model="sonnet", system="sys", images=[b"PNG"]
            )
        self.assertEqual(text, "E=mc^2")
        args = seen["args"]
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotIn("Read", args)
        self.assertNotIn("--resume", args)
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        message = json.loads(seen["stdin"].decode())
        parts = message["message"]["content"]
        self.assertEqual(parts[0]["source"]["data"], base64.b64encode(b"PNG").decode())
        self.assertEqual(parts[-1], {"type": "text", "text": "read it"})

    async def test_run_error_result_is_classified(self):
        async def fake(args, stdin, env, timeout=300):
            return result_line("Claude AI usage limit reached", is_error=True).encode()

        with patch("app.backends.claude.run_process", new=fake):
            with self.assertRaises(RateLimitError):
                await claude.run("x", model="sonnet")

    async def test_stream_yields_deltas_and_requires_final_result(self):
        async def ok(args, stdin, env, timeout=300):
            yield delta_line("1\t가\n")
            yield result_line()

        async def cut(args, stdin, env, timeout=300):
            yield delta_line("1\t가\n")

        with patch("app.backends.claude.stream_process", new=ok):
            self.assertEqual(
                [d async for d in claude.stream("p", model="sonnet")], ["1\t가\n"]
            )
        with patch("app.backends.claude.stream_process", new=cut):
            with self.assertRaises(AIError):
                [d async for d in claude.stream("p", model="sonnet")]

    def test_default_model_is_not_passed_as_flag(self):
        self.assertNotIn("--model", claude._args("default", None, stream=False))
        self.assertIn("opus", claude._args("opus", None, stream=False))
