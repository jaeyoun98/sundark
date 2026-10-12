"""Multi-line display equations. KaTeX treats a newline as a space, so a vision transcription
that kept the image's line breaks as bare newlines rendered as one long line. Lines are joined
with \\\\ inside aligned (on the relation) or gathered."""

import unittest

from app import ai
from app.ai import multiline_latex

SUP = (
    "L^{(j+1)} = \\sup\\{i \\le U^{(j)} : A\\}\n"
    "= \\sup\\{i \\le U^{(j)} : B\\}\n"
    "= \\sup\\{i \\le U^{(j)} : C\\}, \\qquad \\text{for } j \\ge 0,"
)


class MultilineTests(unittest.TestCase):
    def test_lines_starting_with_a_relation_are_aligned_on_it(self):
        self.assertEqual(
            multiline_latex(SUP),
            "\\begin{aligned}L^{(j+1)} &= \\sup\\{i \\le U^{(j)} : A\\} \\\\ "
            "&= \\sup\\{i \\le U^{(j)} : B\\} \\\\ "
            "&= \\sup\\{i \\le U^{(j)} : C\\}, \\qquad \\text{for } j \\ge 0,\\end{aligned}",
        )

    def test_other_line_breaks_are_gathered(self):
        self.assertEqual(
            multiline_latex("a + b\nc + d"),
            "\\begin{gathered}a + b \\\\ c + d\\end{gathered}",
        )

    def test_single_line_and_explicit_environments_are_untouched(self):
        self.assertEqual(multiline_latex("E = mc^2"), "E = mc^2")
        cases = "X=\\begin{cases}\na, & i\\le 0,\\\\\nb, & i>0\n\\end{cases}"
        self.assertEqual(multiline_latex(cases), cases)
        self.assertEqual(multiline_latex("a \\\\\nb"), "a \\\\\nb")

    def test_relation_inside_braces_is_not_used_for_alignment(self):
        self.assertEqual(
            multiline_latex("f_{a=1} = x\n= y"),
            "\\begin{aligned}f_{a=1} &= x \\\\ &= y\\end{aligned}",
        )

    def test_vision_prompt_asks_to_keep_lines(self):
        self.assertIn("aligned", ai._MATH_SYS)
