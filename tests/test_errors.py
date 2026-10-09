import unittest

from app.errors import AIError, ModelUnavailableError, RateLimitError, classify


class ClassifyTests(unittest.TestCase):
    def test_rate_limit_phrases(self):
        for text in (
            "HTTP 429 Too Many Requests",
            "Claude AI usage limit reached",
            "rate_limit_error",
            "You exceeded your current quota",
        ):
            self.assertIs(classify(text), RateLimitError, text)

    def test_unavailable_model_phrases(self):
        for text in (
            "The 'gpt-6.1-luna' model is not supported when using Codex with a ChatGPT account.",
            "model_not_found",
            "Invalid model: foo",
        ):
            self.assertIs(classify(text), ModelUnavailableError, text)

    def test_other_text_is_plain_error(self):
        self.assertIs(classify("connection reset"), AIError)
        self.assertTrue(issubclass(RateLimitError, AIError))
