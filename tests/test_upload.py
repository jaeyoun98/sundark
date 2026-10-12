import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main


class SaveToLibraryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.papers = Path(self.dir.name)
        self.p = patch.object(main, "PAPERS_DIR", self.papers)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.dir.cleanup()

    def _names(self):
        return sorted(f.name for f in self.papers.iterdir())

    def test_saves_with_original_name(self):
        main._save_to_library("Attention.pdf", b"DATA")
        self.assertEqual(self._names(), ["Attention.pdf"])
        self.assertEqual((self.papers / "Attention.pdf").read_bytes(), b"DATA")

    def test_strips_path_components(self):
        main._save_to_library("../../etc/evil.pdf", b"DATA")
        self.assertEqual(self._names(), ["evil.pdf"])  # basename only, cannot escape

    def test_adds_pdf_extension(self):
        main._save_to_library("noext", b"DATA")
        self.assertEqual(self._names(), ["noext.pdf"])

    def test_identical_content_is_not_duplicated(self):
        main._save_to_library("paper.pdf", b"DATA")
        main._save_to_library("paper.pdf", b"DATA")
        self.assertEqual(self._names(), ["paper.pdf"])

    def test_same_name_different_content_gets_suffix(self):
        main._save_to_library("paper.pdf", b"ONE")
        main._save_to_library("paper.pdf", b"TWO")
        self.assertEqual(self._names(), ["paper-1.pdf", "paper.pdf"])
        self.assertEqual((self.papers / "paper.pdf").read_bytes(), b"ONE")
        self.assertEqual((self.papers / "paper-1.pdf").read_bytes(), b"TWO")


if __name__ == "__main__":
    unittest.main()
