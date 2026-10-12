"""Rejoining one visual line the PDF split into fragments (app/pdf_utils._joins_line).

Geometry below is taken from page 1 of arXiv 2506.09762 ("... iterations with O(√d) processors"),
where the radical is drawn ~10 pt above the baseline and used to split the sentence in two."""

import unittest

from app.pdf_utils import _joins_line, _line_gap


def line(left, right, top, bottom, size=10.0):
    return {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "size": size,
        "h": bottom - top,
        "cy": (top + bottom) / 2,
    }


O_PAREN = line(95.9, 175.4, 360.1, 371.7)


class JoinLineTests(unittest.TestCase):
    def test_raised_radical_joins_the_line_it_sits_on(self):
        self.assertTrue(_joins_line(O_PAREN, line(175.8, 183.5, 350.9, 361.9)))

    def test_same_baseline_fragment_still_joins(self):
        self.assertTrue(_joins_line(O_PAREN, line(184.0, 514.0, 358.4, 371.7)))

    def test_other_column_on_the_same_row_stays_apart(self):
        left_col = line(50.0, 290.0, 360.0, 372.0)
        self.assertFalse(_joins_line(left_col, line(310.0, 550.0, 360.0, 372.0)))

    def test_narrow_fragment_far_to_the_right_stays_apart(self):
        self.assertFalse(_joins_line(O_PAREN, line(200.0, 207.0, 351.0, 362.0)))

    def test_narrow_fragment_without_vertical_overlap_stays_apart(self):
        self.assertFalse(_joins_line(O_PAREN, line(175.8, 183.5, 330.0, 341.0)))

    def test_next_wrapped_line_stays_apart(self):
        self.assertFalse(_joins_line(O_PAREN, line(95.9, 300.0, 384.0, 398.0)))


class LineGapTests(unittest.TestCase):
    """Paragraphs join across text blocks only when the vertical gap between lines is small;
    one raised radical must not make two consecutive lines look like they overlap."""

    def test_raised_glyph_does_not_distort_the_gap(self):
        upper = {
            "y0": [345.4, 345.4, 347.2, 347.9, 338.0],
            "y1": [356.4, 356.4, 358.1, 358.8, 349.0],
        }
        lower = {
            "y0": [360.8, 360.8, 360.1, 350.9, 358.4, 360.1, 360.8],
            "y1": [371.7, 371.7, 371.0, 361.9, 369.3, 371.0, 371.7],
        }
        self.assertGreaterEqual(_line_gap(upper, lower), 0)

    def test_plain_lines_gap(self):
        upper = {"y0": [100.0, 100.0], "y1": [110.0, 110.0]}
        lower = {"y0": [114.0, 114.0], "y1": [124.0, 124.0]}
        self.assertAlmostEqual(_line_gap(upper, lower), 4.0)
