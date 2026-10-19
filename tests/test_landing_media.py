"""Release media must fail closed before changing the landing artifacts."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import fetch_landing_media as media


class LandingMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = b"release media"
        self.asset = {
            "file": "translation.mp4",
            "bytes": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }
        self.manifest = {
            "schema_version": 1,
            "repository": media.REPOSITORY,
            "tag": "landing-media-2026-10-15-v1",
            "assets": [{**self.asset, "file": name} for name in sorted(media.FILES)],
        }

    def load(self, manifest):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return media.load_manifest(path)

    def test_rejects_unpinned_sources_and_unexpected_asset_paths(self):
        self.assertEqual(self.load(self.manifest), self.manifest)
        for key, value in (
            ("repository", "other/project"),
            ("tag", "latest"),
            ("tag", "landing-media-2026-10-15-v1/../../other"),
            ("schema_version", 2),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load({**self.manifest, key: value})
        for filename in ("../translation.mp4", "/tmp/translation.mp4", "script.py"):
            manifest = copy.deepcopy(self.manifest)
            manifest["assets"][0]["file"] = filename
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.load(manifest)

    def test_rejects_duplicate_assets_and_invalid_integrity_metadata(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["assets"][1] = manifest["assets"][0]
        with self.assertRaises(ValueError):
            self.load(manifest)
        for key, value in (
            ("bytes", 0),
            ("bytes", True),
            ("bytes", media.MAX_BYTES + 1),
            ("sha256", "not-a-hash"),
        ):
            manifest = copy.deepcopy(self.manifest)
            manifest["assets"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load(manifest)

    def test_verified_download_replaces_file_and_skips_valid_cached_file(self):
        target = self.root / self.asset["file"]
        target.write_bytes(b"old")
        with patch.object(
            media.urllib.request, "urlopen", return_value=io.BytesIO(self.content)
        ) as request:
            media.fetch_asset("https://github.com/example", target, self.asset)
            self.assertEqual(target.read_bytes(), self.content)
            media.fetch_asset("https://github.com/example", target, self.asset)
            self.assertEqual(request.call_count, 1)
        self.assertEqual(list(self.root.glob(".media-*")), [])

    def test_corrupt_truncated_oversized_downloads_preserve_existing_file(self):
        target = self.root / self.asset["file"]
        target.write_bytes(b"old")
        for content in (
            b"x" * len(self.content),
            self.content[:-1],
            self.content + b"extra",
        ):
            with (
                self.subTest(content=content),
                patch.object(
                    media.urllib.request, "urlopen", return_value=io.BytesIO(content)
                ),
                self.assertRaises(ValueError),
            ):
                media.fetch_asset("https://github.com/example", target, self.asset)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(self.root.glob(".media-*")), [])

    def test_network_failure_does_not_leave_partial_artifacts(self):
        target = self.root / self.asset["file"]
        with (
            patch.object(
                media.urllib.request, "urlopen", side_effect=OSError("offline")
            ),
            self.assertRaises(OSError),
        ):
            media.fetch_asset("https://github.com/example", target, self.asset)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".media-*")), [])


if __name__ == "__main__":
    unittest.main()
