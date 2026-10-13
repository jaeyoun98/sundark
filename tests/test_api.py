import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main, settings, store
from app.errors import ModelUnavailableError, RateLimitError

DOC = {"pages": ["PAPER TEXT"], "meta": {"num_pages": 1, "title": "T"}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            patch.dict(os.environ, {"SUNDARK_SETTINGS": str(root / "settings.json")}),
            patch.object(store, "CACHE", root / "cache"),
            patch.object(store, "CHATS", root / "chats"),
            patch("app.main._require_doc", return_value=DOC),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("SUNDARK_BACKEND", None)
        os.environ.pop("PAPER_READER_BACKEND", None)
        settings.save("hermes", {}, 8)
        self.client = TestClient(
            main.app
        )  # not a context manager: skips the model warm-up

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def test_config_with_no_backend_installed(self):
        settings.settings_path().unlink()
        with (
            patch("app.backends.claude.installed", return_value=False),
            patch("app.backends.hermes.installed", return_value=False),
        ):
            body = self.client.get("/api/config").json()
        self.assertIsNone(body["backend"])
        self.assertIn("install", body["reason"].lower())
        self.assertEqual({b["id"] for b in body["backends"]}, {"claude", "hermes"})

    def test_config_picks_the_only_installed_backend_after_a_probe(self):
        settings.settings_path().unlink()
        with (
            patch("app.backends.claude.installed", return_value=True),
            patch("app.backends.hermes.installed", return_value=False),
            patch("app.main.ai.probe", new=AsyncMock()) as probe,
        ):
            body = self.client.get("/api/config").json()
        self.assertEqual(body["backend"], "claude")
        probe.assert_awaited_once()
        self.assertEqual(settings.load()["backend"], "claude")

    def test_settings_save_rejects_unavailable_model(self):
        async def probe(backend, model_id):
            if model_id == "gpt-6.1-luna":
                raise ModelUnavailableError(
                    "This model is not available for your account on this AI Backend"
                )

        with patch("app.main.ai.probe", new=probe):
            r = self.client.post(
                "/api/settings",
                json={
                    "backend": "hermes",
                    "models": {"translate": "gpt-6.1-luna"},
                    "concurrency": 8,
                },
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("gpt-6.1-luna", r.json()["detail"])
        self.assertEqual(
            settings.model_for(settings.load(), "hermes", "translate"), "gpt-6-luna"
        )

    def test_settings_save_probes_each_distinct_model_once(self):
        with patch("app.main.ai.probe", new=AsyncMock()) as probe:
            r = self.client.post(
                "/api/settings",
                json={
                    "backend": "claude",
                    "models": {"chat": "opus"},
                    "concurrency": 4,
                },
            )
        self.assertEqual(r.status_code, 200)
        probed = sorted(call.args[1] for call in probe.await_args_list)
        self.assertEqual(
            probed, ["opus", "sonnet"]
        )  # chat=opus override, everything else sonnet
        self.assertEqual(settings.load()["backend"], "claude")

    def test_cache_keys_differ_per_backend(self):
        with patch("app.main.ai.summarize", new=AsyncMock(return_value="S")):
            self.client.post("/api/summarize", json={"doc_id": "d"})
            settings.save("claude", {}, 8)
            self.client.post("/api/summarize", json={"doc_id": "d"})
        names = sorted(p.name for p in (store.CACHE / "d").iterdir())
        self.assertEqual(
            names, ["summary_claude-sonnet.json", "summary_hermes-gpt-6.1-sol.json"]
        )

    def test_chat_history_survives_backend_switch(self):
        answers = AsyncMock(side_effect=["A1", "A2"])
        with patch("app.main.ai.chat", new=answers):
            self.client.post("/api/chat", json={"doc_id": "d", "question": "Q1"})
            settings.save("claude", {}, 8)
            self.client.post("/api/chat", json={"doc_id": "d", "question": "Q2"})
        second = answers.await_args_list[1]
        self.assertEqual(
            second.kwargs["history"],
            [{"role": "user", "text": "Q1"}, {"role": "assistant", "text": "A1"}],
        )
        turns = self.client.get("/api/chat/d").json()["turns"]
        self.assertEqual([t["text"] for t in turns], ["Q1", "A1", "Q2", "A2"])
        self.client.post("/api/chat/d/reset")
        self.assertEqual(self.client.get("/api/chat/d").json()["turns"], [])

    def test_rate_limit_maps_to_429(self):
        with patch(
            "app.main.ai.summarize", new=AsyncMock(side_effect=RateLimitError("limit"))
        ):
            r = self.client.post("/api/summarize", json={"doc_id": "d"})
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r.json()["rate_limited"])

    def test_stream_rate_limit_line_and_no_cache(self):
        async def stream(segs, lang, brief=""):
            yield "가"  # translate_stream yields translations in order; server numbers by arrival
            raise RateLimitError("limit")

        with (
            patch("app.main.ai.translate_stream", new=stream),
            patch("app.main._get_brief", new=AsyncMock(return_value="")),
        ):
            r = self.client.post(
                "/api/translate_stream",
                json={"doc_id": "d", "page": 0, "segments": ["a", "b"]},
            )
        lines = [json.loads(x) for x in r.text.splitlines()]
        self.assertEqual(lines[0], {"i": 0, "tr": "가"})
        self.assertEqual(lines[-1]["rate_limited"], True)
        self.assertFalse(
            (store.CACHE / "d").exists()
            and any(p.name.startswith("tr_") for p in (store.CACHE / "d").iterdir())
        )

    def test_settings_invalid_values_rejected_before_any_probe(self):
        for body in (
            {
                "backend": "claude",
                "models": {"chat": "bad model id!"},
                "concurrency": 8,
            },
            {"backend": "claude", "models": {"chat": ""}, "concurrency": 8},
            {"backend": "claude", "models": {"bogus": "opus"}, "concurrency": 8},
            {"backend": "claude", "models": {}, "concurrency": 0},
        ):
            with patch("app.main.ai.probe", new=AsyncMock()) as probe:
                r = self.client.post("/api/settings", json=body)
            self.assertEqual(r.status_code, 400, body)
            probe.assert_not_awaited()

    def test_config_reads_backend_info_off_the_event_loop(self):
        import asyncio

        seen = []

        def describe():
            try:
                asyncio.get_running_loop()
                seen.append("on-loop")
            except RuntimeError:
                seen.append("off-loop")
            return {"provider": "openai-codex"}

        with (
            patch("app.backends.hermes.installed", return_value=True),
            patch("app.backends.hermes.describe", new=describe),
        ):
            self.client.get("/api/config")
        self.assertEqual(seen, ["off-loop"])
