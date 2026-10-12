import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main, settings, store
from tests.pdfgen import make_pdf

TITLE = (
    "Two-Dimensional Melting From Liquid-Hexatic Coexistence to Continuous Transitions"
)
RECORD = {
    "title": TITLE,
    "authors": ["S. Kapfer", "W. Krauth"],
    "year": 2015,
    "source": "crossref",
}
PAGE = f"PHYSICAL REVIEW LETTERS\n{TITLE}\nS. Kapfer and W. Krauth\nDOI: 10.1103/PhysRevLett.114.035702"


class PaperApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            patch.dict(
                os.environ, {"PAPER_READER_SETTINGS": str(root / "settings.json")}
            ),
            patch.object(store, "PDFS", root / "pdfs"),
            patch.object(store, "DOCS", root / "docs"),
            patch.object(store, "CACHE", root / "cache"),
            patch.object(store, "CHATS", root / "chats"),
            patch.object(store, "PAPERS", root / "papers"),
            patch("app.main._schedule_prewarm", side_effect=lambda meta: meta),
        ]
        for p in self.patches:
            p.start()
        for d in ("pdfs", "docs", "cache"):
            (root / d).mkdir()
        self.root = root
        settings.save("hermes", {}, 8)
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def upload(self, pdf):
        r = self.client.post(
            "/api/upload", files={"file": ("p.pdf", pdf, "application/pdf")}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_doi_paper_gets_record_title_and_byline(self):
        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=RECORD)):
            meta = self.upload(make_pdf([PAGE]))
        self.assertEqual(meta["paper_id"], "doi:10.1103/physrevlett.114.035702")
        self.assertEqual(meta["title"], TITLE)
        self.assertEqual(meta["byline"], "S. Kapfer, W. Krauth (2015)")

    def test_doi_from_a_citation_is_rejected(self):
        other = {**RECORD, "title": "Deep residual learning for image recognition"}
        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=other)):
            meta = self.upload(make_pdf([PAGE]))
        self.assertEqual(meta["paper_id"], f"file:{meta['doc_id']}")
        self.assertIsNone(store.paper_get("doi:10.1103/physrevlett.114.035702"))
        self.assertEqual(meta["byline"], "")

    def test_offline_open_uses_pdf_title_then_fills_record_later(self):
        pdf = make_pdf([PAGE])
        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=None)):
            first = self.upload(pdf)
        self.assertEqual(first["paper_id"], "doi:10.1103/physrevlett.114.035702")
        self.assertEqual(first["byline"], "")
        with patch(
            "app.main.biblio.lookup", new=AsyncMock(return_value=RECORD)
        ) as lookup:
            second = self.upload(pdf)
        lookup.assert_awaited_once()
        self.assertEqual(second["title"], TITLE)

    def test_two_files_of_one_paper_share_chat(self):
        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=RECORD)):
            a = self.upload(make_pdf([PAGE]))
            b = self.upload(make_pdf([PAGE + "\n(annotated copy)"]))
        self.assertNotEqual(a["doc_id"], b["doc_id"])
        with patch("app.main.ai.chat", new=AsyncMock(return_value="A1")):
            self.client.post(
                "/api/chat", json={"doc_id": a["doc_id"], "question": "Q1"}
            )
        turns = self.client.get(f"/api/chat/{b['doc_id']}").json()["turns"]
        self.assertEqual([t["text"] for t in turns], ["Q1", "A1"])

    def test_legacy_doc_chat_still_visible(self):
        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=RECORD)):
            meta = self.upload(make_pdf([PAGE]))
        doc = store.load_doc(meta["doc_id"])
        del doc["meta"]["paper_id"]  # a doc ingested before Paper IDs existed
        store.save_doc(meta["doc_id"], doc)
        store.chat_clear(meta["paper_id"])
        (self.root / "chats").mkdir(exist_ok=True)
        (self.root / "chats" / f"{meta['doc_id']}.json").write_text(
            json.dumps([{"role": "user", "text": "legacy"}]), "utf-8"
        )
        turns = self.client.get(f"/api/chat/{meta['doc_id']}").json()["turns"]
        self.assertEqual(turns, [{"role": "user", "text": "legacy"}])

    def test_zotero_doi_overrides_pdf_doi(self):
        import asyncio

        with patch("app.main.biblio.lookup", new=AsyncMock(return_value=RECORD)):
            meta = asyncio.run(
                main._ingest(
                    make_pdf([PAGE]), {"key": "ABCD1234", "doi": "10.5555/Zot"}
                )
            )
        self.assertEqual(meta["paper_id"], "doi:10.5555/zot")

    def test_no_identifier_uses_file_fingerprint_without_network(self):
        with patch(
            "app.main.biblio.lookup",
            new=AsyncMock(side_effect=AssertionError("no lookup")),
        ):
            meta = self.upload(make_pdf(["A paper with no identifiers at all"]))
        self.assertEqual(meta["paper_id"], f"file:{meta['doc_id']}")
