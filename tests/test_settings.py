import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"
        self.env = patch.dict(
            os.environ, {"PAPER_READER_SETTINGS": str(self.path)}, clear=False
        )
        self.env.start()
        os.environ.pop("PAPER_READER_BACKEND", None)

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_defaults_when_file_missing(self):
        s = settings.load()
        self.assertIsNone(s["backend"])
        self.assertEqual(settings.model_for(s, "hermes", "translate"), "gpt-6-luna")
        self.assertEqual(settings.model_for(s, "hermes", "chat"), "gpt-6.1-sol")
        self.assertEqual(settings.model_for(s, "claude", "translate"), "sonnet")
        self.assertEqual(settings.model_for(s, "claude", "summary"), "sonnet")
        self.assertEqual(settings.concurrency_for(s, "claude"), 8)

    def test_save_round_trip_keeps_other_backend(self):
        settings.save("hermes", {"translate": "gpt-5.6-luna"}, 4)
        settings.save("claude", {"chat": "opus"}, 2)
        s = settings.load()
        self.assertEqual(s["backend"], "claude")
        self.assertEqual(settings.model_for(s, "hermes", "translate"), "gpt-5.6-luna")
        self.assertEqual(settings.concurrency_for(s, "hermes"), 4)
        self.assertEqual(settings.model_for(s, "claude", "chat"), "opus")

    def test_corrupt_settings_fall_back_to_defaults(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(settings.load()["backend"])
        self.path.write_text(
            json.dumps(
                {
                    "backend": "openai",
                    "models": {"hermes": {"bogus": "x", "chat": 5}},
                    "concurrency": {"hermes": 999},
                }
            ),
            encoding="utf-8",
        )
        s = settings.load()
        self.assertIsNone(s["backend"])
        self.assertEqual(settings.model_for(s, "hermes", "chat"), "gpt-6.1-sol")
        self.assertNotIn("bogus", s["models"]["hermes"])
        self.assertEqual(settings.concurrency_for(s, "hermes"), 16)

    def test_env_overrides_backend(self):
        settings.save("claude", {}, 8)
        with patch.dict(os.environ, {"PAPER_READER_BACKEND": "hermes"}):
            self.assertEqual(settings.load()["backend"], "hermes")

    def test_save_rejects_unknown_backend_and_bad_values(self):
        with self.assertRaises(ValueError):
            settings.save("openai", {}, 8)
        with self.assertRaises(ValueError):
            settings.save("claude", {"chat": "bad model id!"}, 8)
        with self.assertRaises(ValueError):
            settings.save("claude", {}, 0)

    def test_saving_only_pins_models_that_differ_from_defaults(self):
        settings.save("claude", {"chat": "opus"}, 8)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["models"], {"claude": {"chat": "opus"}})
        new_defaults = {
            **settings.DEFAULT_MODELS,
            "claude": {**settings.DEFAULT_MODELS["claude"], "summary": "opus-next"},
        }
        with patch.object(settings, "DEFAULT_MODELS", new_defaults):
            s = settings.load()
        self.assertEqual(settings.model_for(s, "claude", "summary"), "opus-next")
        self.assertEqual(settings.model_for(s, "claude", "chat"), "opus")
