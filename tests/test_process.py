import asyncio
import sys
import unittest

from app.backends.process import run_process, stream_process
from app.errors import AIError, RateLimitError


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_passes_stdin(self):
        out = await run_process(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            b"paper",
            {},
        )
        self.assertEqual(out.strip(), b"PAPER")

    async def test_nonzero_exit_hides_stderr_but_classifies(self):
        args = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('429 secret'); sys.exit(3)",
        ]
        with self.assertRaises(RateLimitError) as ctx:
            await run_process(args, b"", {})
        self.assertNotIn("secret", str(ctx.exception))

    async def test_timeout_kills_child(self):
        args = [sys.executable, "-c", "import time; time.sleep(30)"]
        with self.assertRaises(AIError):
            await run_process(args, b"", {}, timeout=0.5)

    async def test_stream_yields_lines(self):
        args = [sys.executable, "-c", "print('a'); print('b')"]
        lines = [line.strip() async for line in stream_process(args, b"", {})]
        self.assertEqual(lines, ["a", "b"])

    async def test_closing_stream_early_reaps_child(self):
        args = [
            sys.executable,
            "-c",
            "import time\nfor i in range(100):\n print(i, flush=True); time.sleep(0.05)",
        ]
        gen = stream_process(args, b"", {})
        await gen.__anext__()
        await gen.aclose()  # must not hang or leak the child
