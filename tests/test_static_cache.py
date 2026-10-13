"""The UI files must be revalidated on every load. Without Cache-Control, browsers apply
heuristic freshness to app.js, so after an update a normal reload kept running the old code."""

import unittest

from fastapi.testclient import TestClient

from app import main


class StaticCacheTests(unittest.TestCase):
    def test_ui_files_are_revalidated(self):
        client = TestClient(main.app)
        for path in ("/", "/app.js", "/styles.css"):
            r = client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers.get("cache-control"), "no-cache", path)

    def test_api_responses_are_unaffected(self):
        r = TestClient(main.app).get("/api/papers")
        self.assertNotEqual(r.headers.get("cache-control"), "no-cache")
