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

    def test_numbers_beside_prose_or_inside_text_stay(self):
        prose = line("as shown in", 300, 400, 100, 112)
        num = line("(1)", 540, 560, 101, 111)
        self.assertEqual(_attach_equation_numbers([prose, num]), [prose, num])
        eq = line("a = b", 300, 400, 100, 112, math=True)
        ref = line("see Eq. (1) above", 420, 560, 101, 111)
        self.assertEqual(_attach_equation_numbers([eq, ref]), [eq, ref])
