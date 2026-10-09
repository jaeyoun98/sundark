import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node is only needed for JS checks")
class FrontendTests(unittest.TestCase):
    def node(self, script):
        r = subprocess.run(
            ["node", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_syntax(self):
        r = subprocess.run(["node", "--check", "web/app.js"], cwd=ROOT)
        self.assertEqual(r.returncode, 0)

    def test_rate_limit_stops_batch(self):
        self.node("tests/frontend_ratelimit.test.js")

    def test_rate_limit_drains_queued_batches_with_source(self):
        self.node("tests/frontend_ratelimit_queue.test.js")
