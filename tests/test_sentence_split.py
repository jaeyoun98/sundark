"""Enumeration-aware sentence splitting (app/pdf_utils).

The sentence segmenter only breaks a list when its items end in terminal punctuation, so
comma/"and"-joined lists and markerless items stay glued. `_split_on_markers` breaks a
qualifying enumeration run itself, while leaving references and short subfigure phrases intact.
Cases below are taken from real papers (ResNet, ReDex) and the reported figure-caption bug.
"""

import unittest

from app.pdf_utils import (
    _enum_candidates,
    _split_on_markers,
    _starts_lower_frag,
)


def whole(text: str) -> list[str]:
    """Run the marker split over the whole text as one range and return the segment strings."""
    return [text[s:e].strip() for s, e in _split_on_markers([(0, len(text))], text)]


# (text, markers that must each begin a produced segment)
POSITIVES = [
    (
        "Policy Representations. a) Explicit policy with different action representations. "
        "b) Implicit policy learns an energy function that minimizes the energy landscape "
        "c) Diffusion policy refines noise into actions via a learned gradient field.",
        ["a)", "b)", "c)"],
    ),
    (
        "We show that: 1) Our extremely deep residual nets are easy to optimize, but the plain "
        "nets exhibit higher training error when the depth increases; 2) Our deep residual nets "
        "can easily enjoy accuracy gains from greatly increased depth.",
        ["1)", "2)"],
    ),
    (
        "The conv layers follow two simple design rules: (i) for the same output feature map "
        "size, the layers have the same number of filters; and (ii) if the feature map size is "
        "halved, the number of filters is doubled.",
        ["(i)", "(ii)"],
    ),
    (
        "In Table 3 we compare three options: (A) zero-padding shortcuts are used; (B) projection "
        "shortcuts are used; and (C) all shortcuts are projections.",
        ["(A)", "(B)", "(C)"],
    ),
    (
        "ReDex follows a three-stage pipeline: (1) train a proprioception-only policy in "
        "simulation, (2) collect real-world corrections, and (3) train a force-conditioned policy.",
        ["(1)", "(2)", "(3)"],
    ),
]

# text -> expected: NOT split internally (stays one segment)
NEGATIVES = [
    "The shortcut connections in Eqn.(1) introduce neither extra parameter nor computation.",
    "We show (a) Screwdriver Rotation and (b) cellphone Object Flipping.",
    "We evaluate the PASCAL VOC metric (mAP @ IoU = 0.5) and the standard COCO metric.",
    "The dimensions of x and F must be equal in Eqn.(1) and in Eqn.(2).",
]


class EnumSplitTests(unittest.TestCase):
    def test_positive_lists_split_into_items(self):
        for text, markers in POSITIVES:
            segs = whole(text)
            for mk in markers:
                self.assertTrue(
                    any(s.startswith(mk + " ") for s in segs),
                    f"{mk!r} did not begin a segment in: {segs}",
                )

    def test_negatives_are_not_split(self):
        for text in NEGATIVES:
            self.assertEqual(
                whole(text), [text.strip()], f"should stay one segment: {text!r}"
            )

    def test_markerless_run_needs_three(self):
        # Two markers with no colon introduction: a subfigure-style phrase -> no split.
        two = "We compare (a) the baseline and (b) our method."
        self.assertEqual(whole(two), [two])
        # Three markers with no colon -> a real list, split.
        three = "Steps: a) load, b) run, c) save."  # colon present too
        self.assertGreaterEqual(len(whole(three)), 3)

    def test_reference_markers_filtered(self):
        # Eqn.(1)/(2) are references, not list items, so no candidates survive the abbr guard.
        cands = _enum_candidates("as shown in Eqn.(1) and Eqn.(2) above")
        self.assertEqual(cands, [])

    def test_enum_item_not_merged_as_lower_continuation(self):
        # "a) ..." starts lowercase but is a list item, not an inline-math over-split.
        t = "a) first item here"
        self.assertFalse(_starts_lower_frag(t, (0, len(t))))
        # a genuine lowercase continuation is still a continuation.
        t2 = "and then we proceed"
        self.assertTrue(_starts_lower_frag(t2, (0, len(t2))))


if __name__ == "__main__":
    unittest.main()
