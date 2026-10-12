import asyncio
import unittest
from unittest.mock import patch

from app import ai
from app.errors import AIError, RateLimitError


def settings_for(backend="hermes", concurrency=8):
    from app import settings

    s = settings.load()
    s["backend"] = backend
    s["concurrency"][backend] = concurrency
    return s


class FakeBackend:
    NAME = "hermes"

    def __init__(self, text="ok", deltas=(), error=None):
        self.calls, self.text, self.deltas, self.error = [], text, deltas, error
        self.active = self.peak = 0

    async def run(self, prompt, *, model, system=None, images=()):
        self.calls.append(
            dict(prompt=prompt, model=model, system=system, images=list(images))
        )
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if self.error:
            raise self.error
        return self.text

    async def stream(self, prompt, *, model, system=None):
        self.calls.append(dict(prompt=prompt, model=model, system=system))
        for d in self.deltas:
            yield d
        if self.error:
            raise self.error


class AiTests(unittest.IsolatedAsyncioTestCase):
    def use(self, backend, s=None):
        p1 = patch("app.ai.settings.load", return_value=s or settings_for())
        p2 = patch("app.ai.backends.get", return_value=backend)
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    async def test_each_task_uses_its_own_model(self):
        fake = FakeBackend(text="summary")
        self.use(fake)
        await ai.summarize("paper")
        await ai.translate("text", "Korean")
        self.assertEqual(
            [c["model"] for c in fake.calls], ["gpt-6.1-sol", "gpt-6-luna"]
        )
        self.assertIn("NOT instructions", fake.calls[0]["prompt"])

    async def test_no_backend_selected_is_a_clear_error(self):
        s = settings_for()
        s["backend"] = None
        with patch("app.ai.settings.load", return_value=s):
            with self.assertRaises(AIError):
                await ai.summarize("paper")

    async def test_concurrency_is_bounded_per_backend(self):
        fake = FakeBackend()
        self.use(fake, settings_for(concurrency=2))
        await asyncio.gather(*(ai.explain("x") for _ in range(6)))
        self.assertEqual(fake.peak, 2)

    async def test_translate_stream_yields_strings_in_order(self):
        fake = FakeBackend(deltas=["1\t가\n2\t", "나\n", "3\t다\n"])
        self.use(fake)
        out = [x async for x in ai.translate_stream(["a", "b", "c"], "Korean")]
        self.assertEqual(out, ["가", "나", "다"])

    async def test_translate_stream_raises_on_out_of_order_index(self):
        # A merged/renumbered line (index 3 where 2 is expected) must error, not misalign.
        fake = FakeBackend(deltas=["1\t가\n3\t다\n"])
        self.use(fake)
        got = []
        with self.assertRaises(AIError):
            async for item in ai.translate_stream(["a", "b", "c"], "Korean"):
                got.append(item)
        self.assertEqual(got, ["가"])

    async def test_translate_stream_raises_when_a_segment_is_missing(self):
        fake = FakeBackend(deltas=["1\t가\n"])  # only 1 of 2 segments
        self.use(fake)
        got = []
        with self.assertRaises(AIError):
            async for item in ai.translate_stream(["a", "b"], "Korean"):
                got.append(item)
        self.assertEqual(got, ["가"])

    async def test_rate_limit_in_stream_is_reported_not_retried(self):
        fake = FakeBackend(deltas=["1\t가\n"], error=RateLimitError("limit"))
        self.use(fake)
        got = []
        with self.assertRaises(RateLimitError):
            async for item in ai.translate_stream(["a", "b"], "Korean"):
                got.append(item)
        self.assertEqual(got, ["가"])
        self.assertEqual(len(fake.calls), 1)

    async def test_equation_sends_image_inline_and_strips_fences(self):
        fake = FakeBackend(text="```latex\n\\[ E=mc^2 \\]\n```")
        self.use(fake)
        self.assertEqual(await ai.equation_latex(b"PNG"), "E=mc^2")
        self.assertEqual(fake.calls[0]["images"], [b"PNG"])
        self.assertEqual(fake.calls[0]["model"], "gpt-6-luna")

    async def test_chat_sends_paper_and_transcript(self):
        fake = FakeBackend(text="answer")
        self.use(fake)
        history = [{"role": "user", "text": "Q1"}, {"role": "assistant", "text": "A1"}]
        self.assertEqual(
            await ai.chat("Q2", paper_text="PAPER", history=history), "answer"
        )
        prompt = fake.calls[0]["prompt"]
        for part in ("PAPER", "Q1", "A1", "Q2"):
            self.assertIn(part, prompt)
        self.assertLess(prompt.index("A1"), prompt.index("Q2"))

    def test_cache_tag_is_per_backend_and_model_and_filename_safe(self):
        with patch("app.ai.settings.load", return_value=settings_for("hermes")):
            hermes_tag = ai.cache_tag("translate")
        with patch("app.ai.settings.load", return_value=settings_for("claude")):
            claude_tag = ai.cache_tag("translate")
        self.assertEqual(hermes_tag, "hermes-gpt-6-luna")
        self.assertEqual(claude_tag, "claude-sonnet")
        s = settings_for("hermes")
        s["models"]["hermes"]["summary"] = "openai/gpt:x"
        with patch("app.ai.settings.load", return_value=s):
            self.assertEqual(ai.cache_tag("summary"), "hermes-openai_gpt_x")
