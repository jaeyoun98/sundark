"""One persisted output choice governs every prose task and derived cache."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import ai, main, settings, store


class OutputLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.dict(os.environ, {"SUNDARK_SETTINGS": str(root / "settings.json")}),
            patch.object(store, "CACHE", root / "cache"),
            patch.object(store, "CHATS", root / "chats"),
            patch(
                "app.main._require_doc",
                return_value={"pages": ["Source text"], "meta": {"title": "Paper"}},
            ),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_default_validation_and_backend_preservation(self):
        self.assertEqual(settings.load()["output_language"], "Korean")
        for language in settings.OUTPUT_LANGUAGES:
            settings.save("claude", {}, 2, language)
            settings.save("hermes", {}, 4)
            self.assertEqual(settings.load()["output_language"], language)
        before = settings.settings_path().read_bytes()
        with self.assertRaises(ValueError):
            settings.save("claude", {}, 2, "unknown")
        self.assertEqual(settings.settings_path().read_bytes(), before)
        settings.settings_path().write_text('{"output_language":"unknown"}')
        self.assertEqual(settings.load()["output_language"], "Korean")

    def test_settings_api_rejects_before_probe(self):
        with patch("app.main.ai.probe", new=AsyncMock()) as probe:
            response = self.client.post(
                "/api/settings",
                json={"backend": "claude", "output_language": "unknown"},
            )
        self.assertEqual(response.status_code, 400)
        probe.assert_not_awaited()

    def test_summary_cache_is_language_scoped_and_ignores_legacy(self):
        settings.save("claude", {}, 2, "Japanese")
        store.cache_put(
            "doc", f"summary_{ai.cache_tag('summary')}", {"summary": "legacy"}
        )
        with patch(
            "app.main.ai.summarize", new=AsyncMock(side_effect=["日本語", "English"])
        ) as generate:
            self.assertEqual(
                self.client.post("/api/summarize", json={"doc_id": "doc"}).json()[
                    "summary"
                ],
                "日本語",
            )
            self.client.post(
                "/api/summarize", json={"doc_id": "doc", "output_language": "English"}
            )
            self.client.post("/api/summarize", json={"doc_id": "doc"})
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(generate.await_args_list[0].kwargs["target_lang"], "Japanese")
        self.assertEqual(generate.await_args_list[1].kwargs["target_lang"], "English")

    def test_anatomy_cache_is_language_scoped(self):
        settings.save("claude", {}, 2, "Japanese")
        store.cache_put("doc", f"anatomy_{ai.cache_tag('anatomy')}", {"legacy": True})
        with patch(
            "app.main.ai.anatomy",
            new=AsyncMock(return_value={"problem": {"points": []}}),
        ) as generate:
            for language in ("Japanese", "English", "Japanese"):
                response = self.client.post(
                    "/api/anatomy", json={"doc_id": "doc", "output_language": language}
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("legacy", response.json())
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(
            [call.kwargs["target_lang"] for call in generate.await_args_list],
            ["Japanese", "English"],
        )

    def test_stream_repair_keeps_resolved_language_after_settings_change(self):
        for override, expected in ((None, "Japanese"), ("French", "French")):
            with self.subTest(override=override):
                settings.save("claude", {}, 2, "Japanese")

                async def stream(segments, target_lang, brief=""):
                    self.assertEqual(target_lang, expected)
                    settings.save("claude", {}, 2, "English")
                    yield "untranslated source"

                body = {
                    "doc_id": "doc",
                    "page": 0,
                    "segments": ["source"],
                    "force": True,
                }
                if override is not None:
                    body["target_lang"] = override
                with (
                    patch("app.main._get_brief", new=AsyncMock(return_value="")),
                    patch("app.main.ai.translate_stream", new=stream),
                    patch(
                        "app.main.ai.untranslated", side_effect=[True, False]
                    ) as check,
                    patch(
                        "app.main.ai.translate", new=AsyncMock(return_value="repaired")
                    ) as repair,
                ):
                    response = self.client.post("/api/translate_stream", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.text)["tr"], "repaired")
                self.assertEqual(
                    [call.args[2] for call in check.call_args_list],
                    [expected, expected],
                )
                repair.assert_awaited_once_with("source", expected)

    def test_cached_stream_repair_keeps_resolved_language(self):
        import hashlib

        for override, expected in ((None, "Japanese"), ("French", "French")):
            with self.subTest(override=override):
                settings.save("claude", {}, 2, "Japanese")
                signature = hashlib.sha1(b"source").hexdigest()[:10]
                key = f"tr_{expected}_{ai.cache_tag('translate')}_0_{signature}"
                store.cache_put(
                    "doc",
                    key,
                    {
                        "segments": [{"src": "source", "tr": "old"}],
                        "translation": "old",
                    },
                )
                checked = []

                def untranslated(source, translated, target_lang):
                    checked.append(target_lang)
                    settings.save("claude", {}, 2, "English")
                    return translated == "old"

                body = {"doc_id": "doc", "page": 0, "segments": ["source"]}
                if override is not None:
                    body["target_lang"] = override
                with (
                    patch("app.main.ai.untranslated", side_effect=untranslated),
                    patch(
                        "app.main.ai.translate", new=AsyncMock(return_value="repaired")
                    ) as repair,
                    patch("app.main.ai.translate_stream") as stream,
                ):
                    response = self.client.post("/api/translate_stream", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.text)["tr"], "repaired")
                self.assertEqual(checked, [expected, expected])
                repair.assert_awaited_once_with("source", expected)
                stream.assert_not_called()
                self.assertEqual(
                    store.cache_get("doc", key)["segments"][0]["tr"], "repaired"
                )

    def test_all_request_languages_are_validated(self):
        for endpoint, body in (
            ("summarize", {}),
            ("anatomy", {}),
            ("explain", {"text": "x"}),
            ("chat", {"question": "x"}),
            ("translate", {"page": 0}),
            ("translate_stream", {"page": 0}),
            ("brief", {}),
        ):
            key = (
                "target_lang"
                if endpoint in ("translate", "translate_stream", "brief")
                else "output_language"
            )
            with self.subTest(endpoint=endpoint):
                response = self.client.post(
                    f"/api/{endpoint}", json={"doc_id": "doc", key: "unknown", **body}
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.post("/api/review/doc?output_language=unknown").status_code, 400
        )

    def test_language_only_save_does_not_require_provider(self):
        settings.save("claude", {}, 2, "Japanese")
        with patch("app.main.ai.probe", new=AsyncMock()) as probe:
            response = self.client.post(
                "/api/settings",
                json={
                    "backend": "claude",
                    "concurrency": 2,
                    "output_language": "French",
                },
            )
        self.assertEqual(response.status_code, 200)
        probe.assert_not_awaited()
        self.assertEqual(settings.load()["output_language"], "French")

    def test_inflight_summary_keeps_snapshot_and_cache_language(self):
        settings.save("claude", {}, 2, "Japanese")

        async def summarize(text, *, target_lang):
            settings.save("claude", {}, 2, "English")
            return target_lang

        with patch("app.main.ai.summarize", side_effect=summarize):
            response = self.client.post("/api/summarize", json={"doc_id": "doc"})
        self.assertEqual(response.json()["summary"], "Japanese")
        self.assertEqual(
            store.cache_get("doc", f"summary_Japanese_{ai.cache_tag('summary')}")[
                "summary"
            ],
            "Japanese",
        )
        self.assertIsNone(
            store.cache_get("doc", f"summary_English_{ai.cache_tag('summary')}")
        )

    def test_chat_history_survives_language_change(self):
        settings.save("claude", {}, 2, "Japanese")
        with patch(
            "app.main.ai.chat", new=AsyncMock(side_effect=["日本語", "English"])
        ) as chat:
            self.client.post("/api/chat", json={"doc_id": "doc", "question": "First"})
            settings.save("claude", {}, 2, "English")
            self.client.post("/api/chat", json={"doc_id": "doc", "question": "Next"})
        self.assertEqual(chat.await_args.kwargs["target_lang"], "English")
        self.assertEqual(
            chat.await_args.kwargs["history"],
            [
                {"role": "user", "text": "First"},
                {"role": "assistant", "text": "日本語"},
            ],
        )

    def test_review_stages_share_language_snapshot(self):
        settings.save("claude", {}, 2, "Japanese")

        async def summarize(text, *, target_lang):
            settings.save("claude", {}, 2, "English")
            return target_lang

        pdf = Path(self.temp.name) / "doc.pdf"
        pdf.write_bytes(b"pdf")
        with (
            patch("app.main.ai.summarize", side_effect=summarize),
            patch("app.main.ai.anatomy", new=AsyncMock(return_value={})) as anatomy,
            patch(
                "app.main._get_brief", new=AsyncMock(return_value="glossary")
            ) as brief,
            patch(
                "app.main.ai.review", new=AsyncMock(return_value={"body": "note"})
            ) as review,
            patch("app.main.store.pdf_path", return_value=pdf),
            patch(
                "app.main.pdf_utils.figure_catalog",
                return_value={"figures": [], "has_math": False},
            ),
        ):
            response = self.client.post("/api/review/doc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(anatomy.await_args.kwargs["target_lang"], "Japanese")
        self.assertEqual(brief.await_args.args[2], "Japanese")
        self.assertEqual(review.await_args.kwargs["target_lang"], "Japanese")
        self.assertEqual(review.await_args.kwargs["summary"], "Japanese")


class LanguagePromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_prose_and_repair_keep_explicit_language(self):
        with patch("app.ai._run", new=AsyncMock(return_value="ok")) as run:
            await ai.summarize("source", "French")
            await ai.explain("source", "French")
            await ai.translate_brief("source", "French")
            await ai.chat(
                "한국어 질문", paper_text="source", history=[], target_lang="French"
            )
        for call in run.await_args_list:
            self.assertIn("in French", call.args[2])
            self.assertNotIn("in Korean", call.args[2])
        with patch("app.ai._run", new=AsyncMock(side_effect=["broken", "{}"])) as run:
            await ai.anatomy("source", "German")
        for call in run.await_args_list:
            self.assertIn("in German", call.args[2])

    async def test_review_localizes_instructions_without_mutating_materials(self):
        source = "세 줄 요약 배경과 문제 핵심 기여 방법 실험 결과 한계와 의문"
        for language in settings.OUTPUT_LANGUAGES:
            with (
                self.subTest(language=language),
                patch(
                    "app.ai._run",
                    new=AsyncMock(return_value=json.dumps({"body": "note"})),
                ) as run,
            ):
                await ai.review(
                    summary=source,
                    anatomy_obj={},
                    chat_turns=[],
                    figures=[],
                    paper_text=source,
                    target_lang=language,
                )
                prompt, system = run.call_args.args[1:3]
                instructions, materials = prompt.split("<summary>\n", 1)
                self.assertIn(f"in {language}", system)
                self.assertIn(source, materials)
                for heading in ai.REVIEW_HEADINGS[language]:
                    self.assertIn("## " + heading, instructions)
                if language != "Korean":
                    self.assertNotRegex(instructions + system, "[가-힣]")


class FrontendLanguageTests(unittest.TestCase):
    def test_one_settings_control_and_run_snapshot(self):
        html = Path("web/index.html").read_text()
        js = Path("web/app.js").read_text()
        self.assertNotIn('id="lang"', html)
        self.assertEqual(html.count('id="set-language"'), 1)
        self.assertIn("S.aiConfig.output_languages.map", js)
        self.assertIn("const targetLanguage = lang();", js)
        self.assertIn(
            "translateBatch(t.page, t.batch, force, token, targetLanguage)", js
        )
        self.assertIn("target_lang: targetLanguage", js)
        self.assertIn('output_language: $("set-language").value', js)
        self.assertIn("location.reload()", js)
