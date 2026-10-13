import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main, settings, store

DOC = {"pages": ["PAPER TEXT"], "meta": {"num_pages": 2, "title": "Fallback Title"}}


class ReviewHelperTests(unittest.TestCase):
    def test_slug_kebab_and_fallback(self):
        self.assertEqual(
            main._slug("Attention Is All You Need", "d1"), "attention-is-all-you-need"
        )
        self.assertEqual(main._slug("ReAlign: v2.0!", "d1"), "realign-v2-0")
        self.assertEqual(
            main._slug("한글만", "deadbeef"), "deadbeef"
        )  # no ASCII -> fallback
        self.assertEqual(main._slug("", "deadbeef"), "deadbeef")

    def test_referenced_ids_first_seen_order_no_dupes(self):
        body = (
            "## 방법\n![구조](fig-p3-1.png)\n보조 표 ![](tbl-p8-2.png)\n"
            "다시 ![재언급](fig-p3-1.png)\n"
        )
        self.assertEqual(main._referenced_ids(body), ["fig-p3-1", "tbl-p8-2"])

    def test_yaml_value(self):
        self.assertEqual(main._yaml_value(None), "")
        self.assertEqual(main._yaml_value(True), "true")
        self.assertEqual(main._yaml_value(2017), "2017")
        self.assertEqual(main._yaml_value(["NLP", "CV"]), '["NLP", "CV"]')
        self.assertEqual(main._yaml_value('a "b"'), '"a \\"b\\""')

    def test_frontmatter_shape(self):
        rv = {
            "title": "ReAlign",
            "paper_title": "ReAlign: Reranking for RAG",
            "authors": ["A", "B"],
            "venue": "NeurIPS",
            "year": 2017,
            "tags": ["NLP"],
            "summary": "한 줄 요약",
        }
        fm = main._frontmatter(rv, date="2026-10-12T09:00:00+09:00", has_math=True)
        self.assertTrue(fm.startswith("---\n"))
        self.assertIn('title: "ReAlign"', fm)
        self.assertIn("date: 2026-10-12T09:00:00+09:00", fm)  # unquoted
        self.assertIn("draft: true", fm)
        self.assertIn('authors: ["A", "B"]', fm)
        self.assertIn("year: 2017", fm)
        self.assertIn("math: true", fm)
        self.assertIn('arxiv: ""', fm)
        self.assertTrue(fm.rstrip().endswith("---"))

    def test_frontmatter_empty_year_and_lists(self):
        fm = main._frontmatter({"title": "X"}, date="D", has_math=False)
        self.assertIn("year: \n", fm)  # None -> blank value
        self.assertIn("authors: []", fm)
        self.assertIn("tags: []", fm)
        self.assertIn("math: false", fm)


class ReviewEndpointTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        pdf = root / "d1.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")
        self.patches = [
            patch.dict(os.environ, {"SUNDARK_SETTINGS": str(root / "settings.json")}),
            patch.object(store, "CACHE", root / "cache"),
            patch.object(store, "CHATS", root / "chats"),
            patch("app.main._require_doc", return_value=DOC),
            patch("app.main.store.pdf_path", return_value=pdf),
            patch("app.main._get_brief", new=AsyncMock(return_value="")),
            patch("app.main.ai.summarize", new=AsyncMock(return_value="SUMMARY")),
            patch("app.main.ai.anatomy", new=AsyncMock(return_value={})),
            patch(
                "app.main.pdf_utils.figure_catalog",
                return_value={
                    "figures": [
                        {
                            "id": "fig-p1-1",
                            "page": 0,
                            "bbox": (0, 0, 1, 1),
                            "caption": "Fig 1",
                            "kind": "fig",
                        },
                        {
                            "id": "fig-p2-1",
                            "page": 1,
                            "bbox": (0, 0, 1, 1),
                            "caption": "Fig 2",
                            "kind": "fig",
                        },
                    ],
                    "has_math": True,
                },
            ),
            patch("app.main.pdf_utils.crop_region_png", return_value=b"PNGDATA"),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("SUNDARK_BACKEND", None)
        os.environ.pop("PAPER_READER_BACKEND", None)
        settings.save("hermes", {}, 8)
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def _run(self, body):
        with patch(
            "app.main.ai.review",
            new=AsyncMock(
                return_value={
                    "title": "ReAlign Test",
                    "paper_title": "ReAlign",
                    "authors": [],
                    "venue": "",
                    "year": None,
                    "tags": ["NLP"],
                    "summary": "요약",
                    "body": body,
                }
            ),
        ):
            return self.client.post("/api/review/d1")

    def test_zip_contains_only_referenced_and_known_images(self):
        # Body links fig-p1-1 (known -> cropped), fig-p9-9 (unknown -> stripped),
        # and never fig-p2-1 (known but not referenced -> excluded).
        body = "## 방법\n![구조](fig-p1-1.png)\n![없음](fig-p9-9.png)\n끝."
        res = self._run(body)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "application/zip")
        self.assertIn('filename="realign-test.zip"', res.headers["content-disposition"])

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        self.assertEqual(
            set(zf.namelist()),
            {"realign-test/index.md", "realign-test/fig-p1-1.png"},
        )
        md = zf.read("realign-test/index.md").decode("utf-8")
        self.assertIn("![구조](fig-p1-1.png)", md)  # kept
        self.assertNotIn("fig-p9-9", md)  # unknown id stripped so no broken image
        self.assertIn('title: "ReAlign Test"', md)
        self.assertIn("math: true", md)
        self.assertEqual(zf.read("realign-test/fig-p1-1.png"), b"PNGDATA")

    def test_no_images_when_body_references_none(self):
        res = self._run("## 세 줄 요약\n> 한 줄.\n")
        self.assertEqual(res.status_code, 200)
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        self.assertEqual(zf.namelist(), ["realign-test/index.md"])

    def _review_kwargs(self):
        rv = AsyncMock(return_value={"title": "T", "body": "본문"})
        with patch("app.main.ai.review", new=rv):
            self.assertEqual(self.client.post("/api/review/d1").status_code, 200)
        return rv.call_args.kwargs

    def test_review_reads_the_chat_saved_under_the_paper_id(self):
        # Chat History is keyed by Paper ID; a doc without one uses "file:<doc_id>".
        turns = [
            {"role": "user", "text": "왜 이 방법이 되지?"},
            {"role": "assistant", "text": "답"},
        ]
        store.chat_put("file:d1", turns)
        self.assertEqual(self._review_kwargs()["chat_turns"], turns)

    def test_review_reads_a_chat_saved_before_paper_ids(self):
        legacy = [{"role": "user", "text": "예전 질문"}]
        store.CHATS.mkdir(parents=True, exist_ok=True)
        (store.CHATS / "d1.json").write_text(json.dumps(legacy), "utf-8")
        self.assertEqual(self._review_kwargs()["chat_turns"], legacy)

    def test_review_is_written_from_the_paper_text(self):
        self.assertIn("PAPER TEXT", self._review_kwargs()["paper_text"])

    def test_math_is_on_when_the_note_has_equations(self):
        catalog = {"figures": [], "has_math": False}
        with patch("app.main.pdf_utils.figure_catalog", return_value=catalog):
            for body, flag in (
                ("손실은 $$L = x$$ 이다.", "true"),
                ("인라인 \\(x\\) 수식.", "true"),
                ("수식 없음, 가격은 $5.", "false"),
            ):
                md = zipfile.ZipFile(io.BytesIO(self._run(body).content)).read(
                    "realign-test/index.md"
                )
                self.assertIn(f"math: {flag}", md.decode("utf-8"), body)


if __name__ == "__main__":
    unittest.main()
