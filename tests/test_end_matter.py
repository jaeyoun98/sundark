"""End matter (the reference list) is skipped in READING order, and nothing else is.

Diffusion Policy p13: an end-matter heading sits half-way down the RIGHT column, and the left
column below that height ("Related Work" end, "Limitations and Future Work") was dropped with it.
On p16 the appendix title "A" + "Diffusion Policy Implementation Details" comes as two lines; the
title line starts 0.04pt above the "A" line and was dropped as end matter. Acknowledgements are
translated: the range starts at the References heading only."""

import json
import unittest
from unittest.mock import patch

import pymupdf

from app import pdf_utils

TEXT = "the method keeps working well on every page"


def _pdf(right_x: float = 305, appendix_title: str = "") -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)  # page 0: end matter is never looked for here
    p = doc.new_page(width=595, height=842)
    for k in range(28):  # left column, top to bottom
        p.insert_text((50, 90 + 24 * k), f"Left {k} {TEXT}.", fontsize=10)
    for k in range(12):
        p.insert_text((right_x, 90 + 24 * k), f"Right {k} {TEXT}.", fontsize=10)
    p.insert_text((right_x, 400), "References", fontsize=10)
    for k in range(8):
        p.insert_text(
            (right_x, 424 + 24 * k), f"Author {k} (2020) A cited paper.", fontsize=9
        )
    p = doc.new_page(width=595, height=842)
    for k in range(10):
        p.insert_text(
            (50, 90 + 24 * k), f"Author {k + 8} (2020) A cited paper.", fontsize=9
        )
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 90), f"Author 11 (2021) {TEXT}.", fontsize=9)
    if appendix_title:
        p.insert_text((50, 130), appendix_title, fontsize=12)
    else:
        p.insert_text((50, 130), "A", fontsize=12)
        p.insert_text((70, 129.95), "Implementation Details", fontsize=12)  # a hair up
    for k in range(5):
        p.insert_text((50, 154 + 24 * k), f"Appendix {k} {TEXT}.", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def _texts(data: bytes, page: int, skip=None) -> str:
    skip = skip if skip is not None else pdf_utils.end_matter_range(data)
    with patch.object(pdf_utils.layout_model, "detect_page", return_value=[]):
        lay = pdf_utils._page_layout(data, page, skip=skip)
    return " ".join(s["text"] for s in lay["sentences"])


class PartlyCoveredLineTests(unittest.TestCase):
    """A figure/table box covering part of a line decides the whole line by its first word."""

    def _layout(self, regions):
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        p = doc.new_page(width=595, height=842)
        p.insert_text(
            (50, 300), "Here the results are on the COCO test split.", fontsize=10
        )
        p.insert_text(
            (50, 330), "Given a python function and an assert statement.", fontsize=10
        )
        data = doc.tobytes()
        doc.close()
        with patch.object(pdf_utils.layout_model, "detect_page", return_value=regions):
            lay = pdf_utils._page_layout(data, 1)
        return " ".join(s["text"] for s in lay["sentences"])

    def test_a_box_over_the_end_of_a_line_keeps_the_line(self):
        # 2212.00794 p5: the table box reaches over the right part of the caption line
        table = {"role": "table", "x0": 120, "y0": 280, "x1": 545, "y1": 310}
        self.assertIn(
            "Here the results are on the COCO test split.", self._layout([table])
        )

    def test_a_box_over_the_start_of_a_line_drops_the_line(self):
        # 2510.02387 p7: a prompt template in a table box; only its line starts are inside
        table = {"role": "table", "x0": 40, "y0": 315, "x1": 140, "y1": 340}
        text = self._layout([table])
        self.assertNotIn("assert statement", text)
        self.assertIn("Here the results", text)


class EndMatterTests(unittest.TestCase):
    def test_left_column_below_a_right_column_heading_is_kept(self):
        page1 = _texts(_pdf(), 1)
        self.assertIn("Left 27", page1)  # below the heading's height, but before it
        self.assertIn("Right 11", page1)  # above the heading in its own column
        self.assertNotIn("A cited paper", page1)

    def test_a_full_width_caption_above_both_columns_is_kept(self):
        # 2610.04009 p8: a table caption across both columns, References down the left column
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        p = doc.new_page(width=595, height=842)
        cap = (
            "TABLE V: results across levels; all rows are single checkpoints trained on"
        )
        p.insert_text((50, 60), cap + " the same demos.", fontsize=10)
        for k in range(16):
            p.insert_text((50, 100 + 24 * k), f"Left {k} {TEXT}.", fontsize=10)
            p.insert_text(
                (305, 100 + 24 * k), f"Ref {k} (2020) A cited paper.", fontsize=9
            )
        p.insert_text((50, 500), "References", fontsize=10)
        data = doc.tobytes()
        doc.close()
        page1 = _texts(data, 1)
        self.assertIn("trained on the same demos.", page1)
        self.assertIn("Left 15", page1)
        self.assertNotIn("Ref 0", page1)  # the right column comes after References

    def test_one_column_page_still_cuts_at_the_heading(self):
        # lines across the whole page: not two columns, so only the height decides
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        p = doc.new_page(width=595, height=842)
        for k in range(20):
            p.insert_text(
                (50, 90 + 24 * k), f"Line {k} {TEXT} {TEXT} end{k}.", fontsize=10
            )
        p.insert_text((50, 600), "References", fontsize=10)
        p.insert_text((50, 624), f"Author 1 (2020) A cited paper {TEXT}.", fontsize=9)
        data = doc.tobytes()
        self.assertFalse(pdf_utils._two_columns(doc[1]))
        doc.close()
        page1 = _texts(data, 1)
        # the right half of the last line above the heading
        self.assertIn("end19.", page1)
        self.assertNotIn("A cited paper", page1)

    def test_a_right_column_that_starts_just_left_of_the_middle(self):
        # the column split follows _two_columns (middle less 5pt), not the exact middle
        page1 = _texts(_pdf(right_x=295), 1)
        self.assertIn("Left 27", page1)
        self.assertIn("Right 11", page1)
        self.assertNotIn("A cited paper", page1)

    def test_the_skip_range_works_after_a_json_round_trip(self):
        # the server caches it as JSON, so positions come back as lists
        data = _pdf()
        skip = json.loads(json.dumps(pdf_utils.end_matter_range(data)))
        for page in (1, 2, 3):
            self.assertEqual(_texts(data, page, skip), _texts(data, page))

    def test_references_are_skipped_and_the_appendix_title_is_kept(self):
        data = _pdf()
        self.assertEqual(_texts(data, 2), "")
        page3 = _texts(data, 3)
        self.assertIn("Implementation Details", page3)
        self.assertIn("Appendix 0", page3)
        self.assertNotIn("Author 11 (2021)", page3)

    def test_an_uppercase_appendix_heading_ends_the_range(self):
        data = _pdf(appendix_title="APPENDIX")
        page3 = _texts(data, 3)
        self.assertIn("APPENDIX", page3)
        self.assertIn("Appendix 0", page3)
        self.assertNotIn("Author 11 (2021)", page3)


def _doc(*pages) -> bytes:
    """A PDF whose pages after a blank page 0 hold `pages`: lists of (x, y, text, size)."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    for lines in pages:
        p = doc.new_page(width=595, height=842)
        for x, y, text, size in lines:
            p.insert_text((x, y), text, fontsize=size)
    data = doc.tobytes()
    doc.close()
    return data


def _refs(y0: float, n: int, size: float = 9) -> list:
    return [
        (50, y0 + 14 * k, f"Author {k}, B. (2020) A cited paper.", size)
        for k in range(n)
    ]


class RangeTests(unittest.TestCase):
    def test_acknowledgements_are_translated(self):
        data = _doc(
            [
                (50, 90, "Acknowledgements", 12),
                (50, 110, f"We thank everyone. {TEXT}", 10),
            ]
            + [(50, 150, "References", 12)]
            + _refs(170, 20)
        )
        page1 = _texts(data, 1)
        self.assertIn("We thank everyone.", page1)
        self.assertNotIn("A cited paper", page1)

    def test_a_prose_line_that_starts_with_reference_does_not_start_it(self):
        # 2610.02200 p4 lost ten pages from "reference of 17,135 actions." on
        data = _doc(
            [(50, 90, "It uses fewer actions than the human", 10)]
            + [(50, 102, "reference of 17,135 actions.", 10)]
            + [(50, 114, TEXT, 10)],
            [(50, 90, "References", 12)] + _refs(110, 20),
        )
        self.assertEqual(pdf_utils.end_matter_range(data)[0][0], 2)
        self.assertIn("human reference of 17,135 actions.", _texts(data, 1))

    def test_a_references_heading_without_a_list_after_it_is_not_the_start(self):
        data = _doc(
            [(50, 90, "References", 12)]
            + [(50, 110 + 12 * k, TEXT, 10) for k in range(10)]
        )
        self.assertIsNone(pdf_utils.end_matter_range(data))

    def test_an_appendix_letter_set_small_ends_the_range_over_smaller_references(self):
        # 3DGS: "A" at 10pt over 7pt references (an 11pt floor skipped its appendix)
        data = _doc(
            [(50, 90, "References", 10)] + _refs(110, 20, size=7),
            _refs(90, 10, size=7)
            + [(50, 260, "A", 10), (62, 260, "DETAILS OF GRADIENT COMPUTATION", 10)]
            + [(50, 280, TEXT, 10)],
        )
        page2 = _texts(data, 2)
        self.assertIn("DETAILS OF GRADIENT COMPUTATION", page2)
        self.assertIn(TEXT, page2)
        self.assertNotIn("A cited paper", page2)

    def test_a_page_that_is_not_a_reference_list_ends_the_range(self):
        # the Transformer paper's "Attention Visualizations" pages after its references
        caption = (
            "Figure 3: An example of the attention mechanism following dependencies."
        )
        data = _doc(
            [(50, 90, "References", 12)] + _refs(110, 20),
            [(50, 400, caption, 10), (50, 412, TEXT, 10)],
        )
        self.assertEqual(pdf_utils.end_matter_range(data)[1][0], 2)
        self.assertIn("An example of the attention mechanism", _texts(data, 2))


def _one_page(lines, regions, turned=()) -> list[dict]:
    """Sentences of page 1 holding `lines` [(x, y, text, size)] under the model `regions`."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    p = doc.new_page(width=595, height=842)
    for x, y, text, size in lines:
        p.insert_text((x, y), text, fontsize=size)
    for x, y, text in turned:
        p.insert_text((x, y), text, fontsize=10, rotate=90)
    data = doc.tobytes()
    doc.close()
    with patch.object(pdf_utils.layout_model, "detect_page", return_value=regions):
        return pdf_utils._page_layout(data, 1)["sentences"]


def _box(role, cls, x0, y0, x1, y1) -> dict:
    return {"role": role, "cls": cls, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class FurnitureTests(unittest.TestCase):
    """The model's furniture ("abandon") class holds running heads and page numbers, but also
    footnotes: only the first go."""

    def _layout(self, lines, regions, turned=()):
        return _one_page(lines, regions, turned)

    def test_a_footnote_is_kept_and_read_after_the_text_it_sits_in(self):
        # 1512.03385 p2: a footnote at the foot of the left column split the sentence that runs
        # on to the top of the right column
        lines = [(50, 90 + 14 * k, f"Left {k} {TEXT}.", 10) for k in range(5)]
        lines += [(50, 160, "The model is trained on the", 10)]
        lines += [(50, 780, "1 The code is available online.", 8)]
        lines += [(320, 90, "whole dataset for ten epochs.", 10)]
        foot = {"role": "furniture", "x0": 45, "y0": 770, "x1": 290, "y1": 785}
        sentences = self._layout(lines, [foot])
        texts = [s["text"] for s in sentences]
        self.assertIn(
            "The model is trained on the whole dataset for ten epochs.", texts
        )
        self.assertEqual(texts[-1], "1 The code is available online.")
        self.assertTrue(sentences[-1].get("aside"))

    def test_page_numbers_and_a_sideways_stamp_in_furniture_go(self):
        lines = [(50, 300, TEXT, 10), (290, 820 - 40, "12", 10)]
        boxes = [
            {"role": "furniture", "x0": 280, "y0": 770, "x1": 310, "y1": 785},
            {"role": "furniture", "x0": 46, "y0": 400, "x1": 70, "y1": 700},
        ]
        sentences = self._layout(
            lines, boxes, turned=[(60, 650, "arXiv:2610.00001v1 [cs.LG]")]
        )
        self.assertEqual([s["text"] for s in sentences], [TEXT])

    def test_running_heads_match_by_their_letters(self):
        self.assertEqual(
            pdf_utils._norm_header("1:2 • Kerbl et al."),
            pdf_utils._norm_header("• Kerbl et al."),
        )

    def test_a_caption_inside_a_table_box_is_kept(self):
        # ResNet p5: the table box swallowed "Table 5. Error rates ..." and no caption box came
        lines = [
            (60, 300, "method top-1 top-5", 9),
            (60, 312, "ResNet 3.57 1.2", 9),
            (60, 340, "Table 5. Error rates of ensembles on the test set.", 10),
        ]
        table = {"role": "table", "x0": 50, "y0": 285, "x1": 545, "y1": 345}
        sentences = self._layout(lines, [table])
        texts = " ".join(s["text"] for s in sentences)
        self.assertIn("Error rates of ensembles on the test set.", texts)
        self.assertNotIn("ResNet 3.57", texts)
        self.assertTrue(all(s.get("caption") for s in sentences))


class CaptionTests(unittest.TestCase):
    """Figure/table captions are read after the page's text, like footnotes."""

    def test_a_caption_does_not_split_or_stick_to_a_sentence_across_columns(self):
        # Planck p29: "Parameter constraints from the galaxy auto- Fig. 20." | "and cross-..."
        lines = [(50, 90 + 14 * k, f"Left {k} {TEXT}.", 10) for k in range(5)]
        lines += [(50, 160, "The model is trained on the", 10)]
        lines += [(320, 90, "Figure 2: Overview of the method.", 9)]
        lines += [(320, 140, "whole dataset for ten epochs.", 10)]
        cap = _box("caption", "figure_caption", 315, 80, 545, 95)
        sentences = _one_page(lines, [cap])
        texts = [s["text"] for s in sentences]
        self.assertIn(
            "The model is trained on the whole dataset for ten epochs.", texts
        )
        self.assertEqual(texts[-1], "Figure 2: Overview of the method.")
        self.assertTrue(sentences[-1]["aside"] and sentences[-1]["caption"])

    def test_a_caption_box_over_its_first_line_takes_the_whole_caption(self):
        # 2506.09985 p33: the box covered one line, and the caption came out in two pieces
        lines = [(50, 90, TEXT + ".", 10)]
        lines += [
            (
                50,
                200,
                "Table 3: Results of the method on the\ntest split for every task.",
                9,
            )
        ]
        cap = _box("caption", "table_caption", 45, 190, 545, 203)
        texts = [s["text"] for s in _one_page(lines, [cap])]
        self.assertEqual(
            texts[-1],
            "Table 3: Results of the method on the test split for every task.",
        )

    def test_two_captions_do_not_join(self):
        # 2102.09672 p14: "Figure 20. 200 sampling steps ..." + "Figure 21. 400 sampling ..."
        lines = [(50, 300, "Figure 20. Samples at 200 steps", 9)]
        lines += [(50, 330, "Figure 21. Samples at 400 steps", 9)]
        caps = [
            _box("caption", "figure_caption", 45, 290, 545, 303),
            _box("caption", "figure_caption", 45, 320, 545, 333),
        ]
        texts = [s["text"] for s in _one_page(lines, caps)]
        self.assertEqual(
            [t for t in texts if t.startswith("Figure")], ["Figure 20.", "Figure 21."]
        )
        self.assertFalse(any("Figure 20" in t and "Figure 21" in t for t in texts))

    def test_an_equation_number_stays_in_place(self):
        # the model types "(5.4)" as a caption too (formula_caption); it is not moved
        lines = [(50, 300, "We use the identity", 10), (500, 330, "(5.4)", 10)]
        lines += [(50, 360, TEXT + ".", 10)]
        num = _box("caption", "formula_caption", 495, 320, 530, 333)
        sentences = _one_page(lines, [num])
        self.assertFalse(any(s.get("aside") for s in sentences))
        self.assertEqual(sentences[-1]["text"], TEXT + ".")
