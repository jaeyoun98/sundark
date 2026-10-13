"""Zotero routes: browse the library and open a PDF in place with Zotero's DOI and item key."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main, settings, store, zotero
from tests.pdfgen import make_pdf

PAGE = "A Zotero Paper Title\nDOI: 10.9999/from.pdf.text"


class ZoteroApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            patch.dict(os.environ, {"PAPER_READER_SETTINGS": str(root / "settings.json")}),
            patch.object(store, "PDFS", root / "pdfs"), patch.object(store, "DOCS", root / "docs"),
            patch.object(store, "CACHE", root / "cache"), patch.object(store, "CHATS", root / "chats"),
            patch.object(store, "PAPERS", root / "papers"),
            patch.object(main, "PAPERS_DIR", root / "library"),
            patch("app.main._schedule_prewarm", side_effect=lambda meta: meta),
            patch("app.main.biblio.lookup", new=AsyncMock(return_value=None)),
        ]
        for p in self.patches:
            p.start()
        for d in ("pdfs", "docs", "cache", "library"):
            (root / d).mkdir()
        self.root = root
        settings.save("hermes", {}, 8)
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def test_status_and_browse(self):
        with patch("app.main.zotero.available", return_value=True), \
             patch("app.main.zotero.collections", return_value=[{"key": "C1", "name": "CG", "parent": None, "count": 1}]), \
             patch("app.main.zotero.items", return_value={"total": 0, "items": []}) as items:
            self.assertEqual(self.client.get("/api/zotero/status").json(), {"available": True})
            self.assertEqual(self.client.get("/api/zotero/collections").json()["collections"][0]["name"], "CG")
            self.client.get("/api/zotero/items", params={"collection": "C1", "q": "melting", "start": 50})
        items.assert_called_once_with(collection="C1", q="melting", start=50)

    def test_zotero_down_is_a_clear_503(self):
        with patch("app.main.zotero.collections", side_effect=zotero.ZoteroError("Zotero is not running")):
            r = self.client.get("/api/zotero/collections")
        self.assertEqual(r.status_code, 503)
        self.assertIn("not running", r.json()["detail"])

    def test_open_reads_in_place_and_uses_zotero_doi_and_key(self):
        pdf = self.root / "zotero-storage" / "ABCD1234" / "paper.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(make_pdf([PAGE]))
        with patch("app.main.zotero.pdf_path", return_value=pdf), \
             patch("app.main.zotero.item_doi", return_value="10.5555/Zotero.DOI"):
            r = self.client.post("/api/zotero/open", json={"item": "ITEMKEY1", "attachment": "ABCD1234"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["paper_id"], "doi:10.5555/zotero.doi")
        self.assertEqual(list((self.root / "library").iterdir()), [], "not copied into papers/")

    def test_open_without_doi_uses_the_item_key(self):
        pdf = self.root / "p.pdf"
        pdf.write_bytes(make_pdf(["No identifiers in this one"]))
        with patch("app.main.zotero.pdf_path", return_value=pdf), \
             patch("app.main.zotero.item_doi", return_value=None):
            r = self.client.post("/api/zotero/open", json={"item": "ITEMKEY1", "attachment": "ABCD1234"})
        self.assertEqual(r.json()["paper_id"], "zotero:ITEMKEY1")

    def test_missing_file_is_404(self):
        with patch("app.main.zotero.pdf_path", return_value=self.root / "gone.pdf"), \
             patch("app.main.zotero.item_doi", return_value=None):
            r = self.client.post("/api/zotero/open", json={"item": "I", "attachment": "A"})
        self.assertEqual(r.status_code, 404)
