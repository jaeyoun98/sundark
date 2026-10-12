"""AIP/APS 'Universal-GreekwithMathPi' font (1990s-2000s journals) draws brackets and Greek
letters while its text layer says '~1!' for '(1)', 'D' for 'Δ', and so on. The mapping below
was read off rendered glyphs in four real PDFs; letters not seen there are left alone."""

import unittest

from app.pdf_utils import _decode_span, _page_text

MATHPI = "Universal-GreekwithMathPi"


class DecodeSpanTests(unittest.TestCase):
    def test_brackets_and_greek_in_mathpi_are_decoded(self):
        self.assertEqual(_decode_span({"font": MATHPI, "text": "~1!"}), "(1)")
        self.assertEqual(_decode_span({"font": MATHPI, "text": "@#^&<>`;"}), "[]⟨⟩≤≥∞∼")
        self.assertEqual(_decode_span({"font": MATHPI, "text": "DlrhsSV"}), "ΔλρησΣΩ")

    def test_other_fonts_are_untouched(self):
        self.assertEqual(
            _decode_span({"font": "Times-Roman", "text": "~1! @home D"}), "~1! @home D"
        )

    def test_unseen_letters_stay(self):
        self.assertEqual(_decode_span({"font": MATHPI, "text": "bq"}), "bq")


class FakePage:
    def __init__(self, fonts, spans_by_line, plain):
        self._fonts, self._lines, self._plain = fonts, spans_by_line, plain

    def get_fonts(self):
        return [(0, "ttf", "Type1", name, "F0", "") for name in self._fonts]

    def get_text(self, kind="text"):
        if kind == "text":
            return self._plain
        lines = [
            {"spans": [{"font": f, "text": t} for f, t in line]} for line in self._lines
        ]
        return {"blocks": [{"type": 0, "lines": lines}]}


class PageTextTests(unittest.TestCase):
    def test_page_with_mathpi_is_rebuilt_from_decoded_spans(self):
        page = FakePage(
            ["ABCDEE+Times-Roman", "ABCDEF+Universal-GreekwithMathPi"],
            [
                [
                    ("Times-Roman", "if they "),
                    (MATHPI, "~1!"),
                    ("Times-Roman", " lead to"),
                ],
                [("Times-Roman", "assumption "), (MATHPI, "~4!")],
            ],
            "if they ~1! lead to\nassumption ~4!\n",
        )
        self.assertEqual(_page_text(page), "if they (1) lead to\nassumption (4)\n")

    def test_page_without_mathpi_keeps_the_plain_extraction(self):
        page = FakePage(["Times-Roman"], [], "plain text ~ ! kept\n")
        self.assertEqual(_page_text(page), "plain text ~ ! kept\n")
