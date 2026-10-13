"""Word boxes come from the PDF's own glyph positions, and TeX's CMEX10 symbol font is read as
the symbols it draws."""

import unittest
from unittest.mock import patch

import pymupdf

from app import pdf_utils
from app.pdf_utils import (
    _cmap_codes,
    _cmex_tables,
    _decode_span,
    _page_text,
    _words_with_style,
)

CMEX = "CMEX10"

# pdfTeX's built-in ToUnicode map for cmex10, cut down: tall-delimiter pieces and a few others
PDFTEX_CMAP = """begincmap
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfrange
<D7> <D8> <03A5>
endbfrange
3 beginbfchar
<30> <F8EB>
<31> <F8F6>
<9F> <221A>
endbfchar
endcmap"""

# entries of the full map in 2609.26280's CMEX10
FULL_CMAP = """begincmap
6 beginbfchar
<00> <0028FE00>
<28> <007BFE03>
<29> <007DFE03>
<50> <2211FE00>
<7B> <0020>
<7C> <23DF>
endbfchar
endcmap"""


class FakePage:
    """A page with one CMEX10 font whose ToUnicode CMap is `cmap` (None: no map)."""

    def __init__(self, cmap=None, spans=()):
        self.cmap, self.spans, self.parent = cmap, list(spans), self

    def get_fonts(self):
        return [(7, "pfb", "Type1", "ABCDEF+CMEX10", "F1", "")]

    def xref_get_key(self, xref, key):
        return ("xref", "9 0 R") if self.cmap is not None else ("null", "null")

    def xref_stream(self, xref):
        return self.cmap.encode()

    def get_text(self, kind="text"):
        return {"blocks": [{"type": 0, "lines": [{"spans": self.spans}]}]}


def _one_span_page(text: str):
    """A page whose text is one span, the way a justified line often comes out of a PDF."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), text, fontname="helv", fontsize=10)
    return doc, page


class WordBoxTests(unittest.TestCase):
    def test_word_boxes_follow_the_glyphs_not_the_character_count(self):
        # Wide and narrow letters in one span: splitting the span by character count put the
        # start of "Here," about 10pt to the right of its "H", inside "illicit." before it.
        doc, page = _one_span_page("WWWWWWWWWW illicit. Here, we go")
        real = {w[4]: w for w in page.get_text("words")}
        words = {w["t"]: w for w in _words_with_style(page)}
        for t in ("WWWWWWWWWW", "illicit.", "Here,", "we", "go"):
            self.assertAlmostEqual(words[t]["x0"], real[t][0], delta=0.5, msg=t)
            self.assertAlmostEqual(words[t]["x1"], real[t][2], delta=0.5, msg=t)
        self.assertLess(words["illicit."]["x1"], words["Here,"]["x0"])
        doc.close()

    def test_word_order_size_baseline_and_line_keys_are_kept(self):
        doc, page = _one_span_page("alpha beta gamma")
        words = _words_with_style(page)
        self.assertEqual([w["t"] for w in words], ["alpha", "beta", "gamma"])
        self.assertTrue(all(w["size"] == 10.0 for w in words))
        self.assertTrue(all(w["base"] == 100.0 for w in words))  # inserted on y=100
        self.assertEqual({(w["bno"], w["lno"]) for w in words}, {(0, 0)})
        doc.close()

    def test_a_span_whose_decoded_text_changes_length_falls_back_to_the_split(self):
        doc, page = _one_span_page("alpha beta")
        real = {w[4]: w for w in page.get_text("words")}
        # pretend the font drops a glyph when decoded (as CMEX pieces do)
        with patch("app.pdf_utils._decode_span", return_value="alpha bet"):
            words = _words_with_style(page)
        self.assertEqual([w["t"] for w in words], ["alpha", "bet"])
        self.assertAlmostEqual(words[0]["x0"], real["alpha"][0], delta=0.5)
        self.assertLessEqual(words[1]["x1"], real["beta"][2] + 0.5)
        doc.close()


class CmexDecodeTests(unittest.TestCase):
    def test_big_operators_radicals_and_wide_accents(self):
        self.assertEqual(_decode_span({"font": CMEX, "text": "PQRXYZ"}), "∑∏∫∑∏∫")
        self.assertEqual(_decode_span({"font": CMEX, "text": "pqrs"}), "√√√√")
        self.assertEqual(_decode_span({"font": "ABCDEF+CMEX10", "text": "bef"}), "ˆ˜˜")

    def test_big_delimiters_including_the_control_codes(self):
        self.assertEqual(
            _decode_span({"font": CMEX, "text": "\x00\x01\x12\x13"}), "()()"
        )
        self.assertEqual(_decode_span({"font": CMEX, "text": "hi"}), "[]")

    def test_raw_brace_codes_without_a_map(self):
        # "(" is OMX code 40, a big "{"; "{", "|", "}" are pieces of an underbrace
        self.assertEqual(_decode_span({"font": CMEX, "text": "()"}), "{}")
        self.assertEqual(_decode_span({"font": CMEX, "text": "|{z}"}), "")

    def test_pdftex_builtin_map_leaves_the_other_codes_raw(self):
        # pdfTeX's map covers only the tall-delimiter pieces: "(" and "P" still read raw
        page = FakePage(PDFTEX_CMAP)
        tables = _cmex_tables(page)
        self.assertEqual(_decode_span({"font": CMEX, "text": "(P)"}, tables), "{∑}")

    def test_codes_a_full_map_covers_are_kept(self):
        # 2609.26280: a full ToUnicode map reads a big "(" (code 0) as "(" + U+FE00 and code
        # 40 as "{" + U+FE03, so its "(" is a real parenthesis
        page = FakePage(FULL_CMAP)
        text = "(\ufe00 )\ufe00 ∑\ufe00"
        self.assertEqual(
            _decode_span({"font": CMEX, "text": text}, _cmex_tables(page)), text
        )

    def test_cmap_codes_reads_bfchar_and_bfrange(self):
        codes = _cmap_codes(PDFTEX_CMAP)
        self.assertEqual(codes, {0x30, 0x31, 0x9F, 0xD7, 0xD8})
        self.assertEqual(_cmap_codes(""), frozenset())
        # a range may list its targets in an array; the array's items are not codes
        ranges = "beginbfrange\n<41> <42> [<0061> <0062>]\n<50> <50> <2211>\nendbfrange"
        self.assertEqual(_cmap_codes(ranges), {0x41, 0x42, 0x50})

    def test_a_tall_delimiter_keeps_one_char_for_its_pieces(self):
        # top piece, extension, bottom piece of a tall "[" and of a tall "("
        self.assertEqual(_decode_span({"font": CMEX, "text": "264"}), "[")
        self.assertEqual(_decode_span({"font": CMEX, "text": "0B@"}), "(")

    def test_other_fonts_are_untouched(self):
        self.assertEqual(_decode_span({"font": "CMR10", "text": "PQR bep"}), "PQR bep")

    def test_page_text_of_a_page_with_cmex_is_decoded(self):
        def spans(t):
            return [
                {"font": "CMR10", "text": "a "},
                {"font": "CMEX10", "text": t},
                {"font": "CMR10", "text": " b"},
            ]

        self.assertEqual(_page_text(FakePage(None, spans("P("))), "a ∑{ b\n")
        # the page's own map decides: a full map's "(" is a real parenthesis
        self.assertEqual(
            _page_text(FakePage(FULL_CMAP, spans("(\ufe00"))), "a (\ufe00 b\n"
        )


class MarginTests(unittest.TestCase):
    def test_a_margin_word_goes_only_with_its_whole_line(self):
        # NeRF: a narrow page whose body lines start at x=34, inside the 44pt side margin
        doc = pymupdf.open()
        doc.new_page(width=414, height=630)
        p = doc.new_page(width=414, height=630)
        p.insert_text(
            (34, 300), "to colors has also been used for graphics.", fontsize=10
        )
        p.insert_text((20, 500), "arXiv:2003.08934v2", fontsize=10, rotate=90)
        data = doc.tobytes()
        doc.close()
        with patch.object(pdf_utils.layout_model, "detect_page", return_value=[]):
            lay = pdf_utils._page_layout(data, 1)
        text = " ".join(s["text"] for s in lay["sentences"])
        self.assertIn("to colors has also been used", text)
        self.assertNotIn("arXiv", text)
