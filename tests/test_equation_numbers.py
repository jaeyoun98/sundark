"""Equation numbers ('(1)') sit alone at the right edge of a display equation's row. As a
one-token line they were dropped by the short-fragment filter, so every equation lost its
number. _attach_equation_numbers moves each onto the equation line it sits beside."""

import unittest

from app.pdf_utils import _attach_equation_numbers


def line(text, left, right, top, bottom, math=False):
    return {
        "text": text,
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "math": math,
    }


class EquationNumberTests(unittest.TestCase):
    def test_number_beside_an_equation_is_attached_and_removed(self):
        eq = line("x n 5 A x n 2 1 ,", 332, 378, 420, 433, math=True)
        num = line("(1)", 548, 560, 421, 432)
        prose = line("where the probability that the system", 314, 560, 440, 452)
        out = _attach_equation_numbers([eq, num, prose])
        self.assertEqual(out, [eq, prose])
        self.assertEqual(eq["eqno"], "1")

    def test_letter_and_dotted_numbers(self):
        for text, want in (("(4a)", "4a"), ("(2.3)", "2.3"), ("( 7 )", "7")):
            eq = line("a = b", 300, 400, 100, 112, math=True)
            _attach_equation_numbers([eq, line(text, 540, 560, 101, 111)])
            self.assertEqual(eq["eqno"], want, text)

    def test_number_not_level_with_an_equation_stays(self):
        eq = line("a = b", 300, 400, 100, 112, math=True)
        num = line("(1)", 540, 560, 150, 161)
        self.assertEqual(_attach_equation_numbers([eq, num]), [eq, num])
        self.assertNotIn("eqno", eq)

    def test_unmatched_number_does_not_replace_an_existing_number(self):
        eq = line("x = a", 80, 140, 100, 112, math=True)
        number = line("(1)", 270, 285, 101, 111)
        unmatched = line("(2)", 540, 555, 101, 111)
        self.assertEqual(
            _attach_equation_numbers([eq, number, unmatched]), [eq, unmatched]
        )
        self.assertEqual(eq["eqno"], "1")

    def test_numbers_beside_prose_or_inside_text_stay(self):
        prose = line("as shown in", 300, 400, 100, 112)
        num = line("(1)", 540, 560, 101, 111)
        self.assertEqual(_attach_equation_numbers([prose, num]), [prose, num])
        eq = line("a = b", 300, 400, 100, 112, math=True)
        ref = line("see Eq. (1) above", 420, 560, 101, 111)
        self.assertEqual(_attach_equation_numbers([eq, ref]), [eq, ref])


class EquationLayoutTests(unittest.TestCase):
    def layout_equations(self, entries, boxes):
        from unittest.mock import patch

        import pymupdf

        from app import pdf_utils

        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        page = doc.new_page(width=595, height=842)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=10)
        data = doc.tobytes()
        doc.close()
        regions = [
            dict(role="formula", x0=x0, y0=y0, x1=x1, y1=y1) for x0, y0, x1, y1 in boxes
        ]
        with patch.object(pdf_utils.layout_model, "detect_page", return_value=regions):
            return pdf_utils._page_layout(data, 1)["sentences"]

    def test_consecutive_numbered_equations_keep_their_boundaries(self):
        sentences = self.layout_equations(
            [
                (80, 110, "x = a"),
                (270, 110, "(1)"),
                (80, 135, "y = b"),
                (270, 135, "(2)"),
            ],
            [(70, 95, 180, 115), (70, 120, 180, 140)],
        )
        self.assertEqual(
            [(s["text"], s.get("eqno")) for s in sentences],
            [("x = a", "1"), ("y = b", "2")],
        )

    def test_two_numbers_in_one_region_do_not_overwrite_each_other(self):
        sentences = self.layout_equations(
            [
                (80, 110, "x = a"),
                (270, 110, "(1)"),
                (80, 135, "y = b"),
                (270, 135, "(2)"),
            ],
            [(70, 95, 180, 140)],
        )
        self.assertEqual([s.get("eqno") for s in sentences], ["1", "2"])

    def test_multiline_equation_remains_one_segment(self):
        sentences = self.layout_equations(
            [(80, 110, "x = a"), (80, 125, "+ b"), (270, 118, "(3)")],
            [(70, 95, 180, 130)],
        )
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0]["eqno"], "3")
        self.assertEqual(sentences[0]["text"], "x = a + b")

    def test_same_row_numbers_attach_to_the_nearest_column(self):
        left = line("x = a", 80, 140, 100, 112, math=True)
        right = line("y = b", 350, 410, 100, 112, math=True)
        nleft = line("(1)", 270, 285, 101, 111)
        nright = line("(2)", 540, 555, 101, 111)
        _attach_equation_numbers([left, nleft, right, nright])
        self.assertEqual(left["eqno"], "1")
        self.assertEqual(right["eqno"], "2")
