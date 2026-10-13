"""Inline sub/superscripts are marked as ^{...} / _{...} in the text sent for translation.

The PDF text layer reads "{ã^j}_{j=1}^{N_neg}" as "{ ˜ a j } N neg j = 1": without marks the
translator has to guess which run is which. A run is marked only when it is attached to the
glyph before it, so an inline fraction (a small numerator above, a small denominator below,
after a gap) is not mistaken for scripts. Geometry is from Diffusion Policy p6 and Adam p4."""

import copy
import unittest
from unittest.mock import patch

import pymupdf

from app import pdf_utils
from app.pdf_utils import _mark_scripts, _script_roles


def word(x0, x1, t, size=10.0, base=152.0, line=0):
    return {"x0": x0, "x1": x1, "t": t, "size": size, "base": base, "line": line}


def roles(ws):
    words = [{"x0": w["x0"], "x1": w["x1"], "t": w["t"]} for w in ws]
    styled = [{"size": w["size"], "base": w["base"]} for w in ws]
    line_of = {g: w["line"] for g, w in enumerate(ws)}
    return _script_roles(list(range(len(ws))), words, styled, line_of, 10.0)


def text_of(ws, marks):
    text, local, pos = "", [], 0
    for g, w in enumerate(ws):
        local.append((len(text), len(text) + len(w["t"]), g))
        text += w["t"] + " "
    return _mark_scripts(text, 0, len(text), local, marks)


# "samples { ˜ a j } N neg j = 1 are used"
DP = [
    word(143.8, 175.9, "samples"),
    word(178.2, 183.2, "{"),
    word(182.9, 188.4, "˜", base=161.0),  # wide tilde: body size, its own odd baseline
    word(183.2, 188.2, "a"),
    word(189.3, 191.3, "j", size=7.4, base=148.5),
    word(192.0, 197.0, "}"),
    word(197.0, 201.9, "N", size=7.4, base=146.5),
    word(201.7, 210.1, "neg", size=6.0, base=147.5),
    word(198.1, 200.5, "j", size=7.4, base=155.0),
    word(200.5, 205.0, "=", size=7.4, base=155.0),
    word(205.0, 208.0, "1", size=7.4, base=155.0),
    word(212.0, 225.0, "are"),
    word(227.0, 241.0, "used"),
]


class ScriptMarkTests(unittest.TestCase):
    def test_attached_and_stacked_scripts_are_marked(self):
        self.assertEqual(
            text_of(DP, roles(DP)),
            "samples { ˜ a ^{j} } ^{N neg} _{j = 1} are used",
        )

    def test_a_subscript_then_a_superscript(self):
        ws = [
            word(50, 56, "x"),
            word(56.3, 58, "i", size=7.0, base=154.0),
            word(58.2, 61, "2", size=7.0, base=148.0),
            word(64, 70, "is"),
        ]
        self.assertEqual(text_of(ws, roles(ws)), "x _{i} ^{2} is")

    def test_an_inline_fraction_after_a_gap_is_left_alone(self):
        # "γ ≜ β²₁/√β₂": numerator and denominator are small and stacked, after "≜" and a gap
        ws = [
            word(50, 56, "γ"),
            word(58, 66, "≜"),
            word(69, 74, "β", size=7.0, base=148.0),
            word(69, 76, "√", size=7.0, base=157.0),
            word(80, 90, "and"),
        ]
        self.assertEqual(roles(ws), {})

    def test_small_text_after_an_opening_bracket_is_not_a_script(self):
        ws = [word(50, 53, "("), word(53.2, 56, "1", size=7.0, base=148.0)]
        self.assertEqual(roles(ws), {})

    def test_small_text_on_the_baseline_is_not_a_script(self):
        # small caps / a smaller font on the same baseline
        ws = [word(50, 70, "Diffusion"), word(70.5, 90, "POLICY", size=7.0, base=152.0)]
        self.assertEqual(roles(ws), {})

    def test_a_script_on_another_line_is_not_attached(self):
        ws = [word(50, 70, "value"), word(40, 45, "2", size=7.0, base=140.0, line=1)]
        self.assertEqual(roles(ws), {})

    def test_marks_follow_the_sentence_range_and_strip(self):
        text = "Let k cmp q denote it. "
        local = [(0, 3, 0), (4, 5, 1), (6, 9, 2), (10, 11, 3), (12, 18, 4), (19, 22, 5)]
        marks = {2: "^", 3: "_"}
        self.assertEqual(
            _mark_scripts(text, 0, 23, local, marks), "Let k ^{cmp} _{q} denote it."
        )
        self.assertEqual(_mark_scripts(text, 4, 12, local, marks), "k ^{cmp} _{q}")


class LayoutScriptTests(unittest.TestCase):
    def test_page_layout_marks_a_raised_script_in_tex_only(self):
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        p = doc.new_page(width=595, height=842)
        head = "We set the weight x"
        w = pymupdf.get_text_length(head, fontname="helv", fontsize=10)
        w2 = pymupdf.get_text_length("2", fontname="helv", fontsize=7)
        p.insert_text((50, 300), head, fontsize=10)
        p.insert_text((50 + w + 0.3, 296), "2", fontsize=7)  # raised, right after "x"
        p.insert_text(
            (50 + w + 0.3 + w2 + 2.5, 300), "for every layer of the net.", fontsize=10
        )
        data = doc.tobytes()
        doc.close()
        with patch.object(pdf_utils.layout_model, "detect_page", return_value=[]):
            (s,) = pdf_utils._page_layout(data, 1)["sentences"]
        self.assertEqual(s["text"], "We set the weight x 2 for every layer of the net.")
        self.assertEqual(
            s["tex"], "We set the weight x ^{2} for every layer of the net."
        )

    def test_layout_joins_tex_across_a_page_break(self):
        doc = pymupdf.open()
        doc.new_page()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        word = {"x0": 50, "y0": 700, "x1": 60, "y1": 710, "t": "w"}
        sent = {"words": [0], "para": 0, "size": 10, "heading": False}
        cores = [
            {
                "words": [word],
                "sentences": [
                    {**sent, "text": "we set x 2 and", "tex": "we set x ^{2} and"}
                ],
            },
            {"words": [word], "sentences": [{**sent, "text": "then it stops."}]},
        ]

        def fake(pdf_bytes, i, headers=None, skip=None):
            return copy.deepcopy(cores[i])

        with patch.object(pdf_utils, "_page_layout", side_effect=fake):
            first = pdf_utils.layout(data, 0)["sentences"][-1]
            second = pdf_utils.layout(data, 1)["sentences"]
        self.assertEqual(first["text"], "we set x 2 and then it stops.")
        self.assertEqual(first["tex"], "we set x ^{2} and then it stops.")
        self.assertEqual(second, [])  # the continuation is shown on the first page only
