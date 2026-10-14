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

    def test_pdf_wheel_zoom_keeps_pointer_and_browser_zoom_boundaries(self):
        self.node("tests/frontend_pdf_zoom.test.js")

    def test_copying_math_yields_latex(self):
        # KaTeX's copy-tex makes a copied formula paste as its LaTeX source instead of the
        # hidden MathML plus one line per glyph; it must match the loaded KaTeX version.
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        import re

        versions = set(re.findall(r"KaTeX/([\d.]+)/", html))
        self.assertEqual(len(versions), 1, versions)
        self.assertIn(f"KaTeX/{versions.pop()}/contrib/copy-tex.min.js", html)
