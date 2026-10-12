import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store


class PaperStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            patch.object(store, "PAPERS", root / "papers"),
            patch.object(store, "CHATS", root / "chats"),
            patch.object(store, "CACHE", root / "cache"),
        ]
        for p in self.patches:
            p.start()
        self.root = root

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def test_paper_round_trip(self):
        self.assertIsNone(store.paper_get("doi:10.1/x"))
        store.paper_put(
            "doi:10.1/x",
            {"title": "T", "authors": [], "year": 2020, "source": "crossref"},
        )
        self.assertEqual(store.paper_get("doi:10.1/x")["title"], "T")
        self.assertEqual(store.paper_get("doi:10.1/x")["paper_id"], "doi:10.1/x")

    def test_unsafe_paper_ids_map_to_safe_files(self):
        pid = "doi:10.1002/(sici)1097-4636;2-<../../x>"
        store.paper_put(pid, {"title": "T"})
        store.chat_put(pid, [{"role": "user", "text": "q"}])
        files = list((self.root / "papers").iterdir()) + list(
            (self.root / "chats").iterdir()
        )
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertRegex(f.name, r"^[0-9a-f]{16}\.json$")
        self.assertEqual(store.chat_get(pid), [{"role": "user", "text": "q"}])

    def test_chat_migrates_from_doc_id(self):
        (self.root / "chats").mkdir()
        old = [
            {"role": "user", "text": "old q"},
            {"role": "assistant", "text": "old a"},
        ]
        (self.root / "chats" / "abcdef0123456789.json").write_text(
            json.dumps(old), "utf-8"
        )
        store.chat_migrate("abcdef0123456789", "doi:10.1/x")
        self.assertEqual(store.chat_get("doi:10.1/x"), old)
        self.assertFalse((self.root / "chats" / "abcdef0123456789.json").exists())

    def test_migration_never_overwrites_an_existing_paper_chat(self):
        store.chat_put("doi:10.1/x", [{"role": "user", "text": "kept"}])
        (self.root / "chats" / "abcdef0123456789.json").write_text("[]", "utf-8")
        store.chat_migrate("abcdef0123456789", "doi:10.1/x")
        self.assertEqual(
            store.chat_get("doi:10.1/x"), [{"role": "user", "text": "kept"}]
        )

    def test_paper_cache_id_is_shared_by_one_paper(self):
        cid = store.paper_cache_id("arxiv:2506.09762")
        store.cache_put(cid, "brief_Korean_x", {"brief": "G"})
        self.assertEqual(
            store.cache_get(store.paper_cache_id("arxiv:2506.09762"), "brief_Korean_x"),
            {"brief": "G"},
        )
        self.assertNotEqual(cid, store.paper_cache_id("arxiv:2506.09763"))
