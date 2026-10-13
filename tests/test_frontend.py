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

    def test_markdown_keeps_latex_for_katex(self):
        self.node("tests/frontend_markdown_math.test.js")

    def test_copying_math_yields_latex(self):
        # app.js owns the copy handler (both text/plain and text/html get LaTeX). KaTeX's
        # copy-tex must stay out: it rewrites text/html with the doubled KaTeX markup.
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("copy-tex", html)
        self.assertIn('document.addEventListener("copy"', js)
        self.assertIn('setData("text/html"', js)

    def test_math_is_upgraded_per_page(self):
        self.node("tests/frontend_math_per_page.test.js")

    def test_unrenderable_math_is_not_swapped_in(self):
        self.node("tests/frontend_math_guard.test.js")
