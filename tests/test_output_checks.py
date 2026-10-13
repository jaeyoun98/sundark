"""Checks on model output before the reader trusts it: a vision transcription must be valid
LaTeX, and a translation into Korean/Japanese/Chinese must actually be in that language."""

import unittest

from app.ai import latex_ok, untranslated

BAD_TR = (
    "where the probability that the system is in state \\(i\\) at \\(n\\) MCS is given by the "
    "\\(i\\)번째 성분 of the vector \\(\\mathbf{x}^n\\), and \\(A\\) is the 전이 행렬."
)
BAD_SRC = (
    "where the probability that the system is in state i at n MCS is given by the i th "
    "component of the vector vector x n , and A is the transition matrix."
)


class LatexOkTests(unittest.TestCase):
    def test_valid_latex(self):
        for tex in (
            "\\mathbf{x}_n = A\\mathbf{x}_{n-1},",
            "\\{a\\} \\cup \\{b\\}",
            "X=\\begin{cases}a & b\\\\ c & d\\end{cases}",
            "\\begin{aligned}a &= b \\\\ &= c\\end{aligned}",
        ):
            self.assertTrue(latex_ok(tex), tex)

    def test_invalid_latex(self):
        for tex in (
            "\\mathbf{x}_n = A\\mathbf{x}_{n-1},\\end{final",
            "a_{b",
            "a}",
            "\\begin{aligned}a\\end{cases}",
            "",
        ):
            self.assertFalse(latex_ok(tex), tex)


class UntranslatedTests(unittest.TestCase):
    def test_mostly_english_korean_translation_is_flagged(self):
        self.assertTrue(untranslated(BAD_SRC, BAD_TR, "Korean"))

    def test_korean_with_english_terms_passes(self):
        ok = (
            "\\(n\\) Monte Carlo steps (MCS)에서의 계 상태는 \\(n-1\\) MCS에서의 상태와 "
            "다음 전이 행렬로 연결된다."
        )
        self.assertFalse(
            untranslated(
                "The state of the system at n Monte Carlo steps is related "
                "to the state at n-1 MCS by the transition matrix.",
                ok,
                "Korean",
            )
        )
        self.assertFalse(
            untranslated(
                "We use the Metropolis-Hastings algorithm with Gaussian proposals.",
                "가우시안 제안 분포를 쓰는 Metropolis-Hastings 알고리즘을 사용한다.",
                "Korean",
            )
        )

    def test_short_or_symbolic_sources_are_never_flagged(self):
        self.assertFalse(
            untranslated(
                "Phys. Rev. Lett. 114, 035702 (2015).",
                "Phys. Rev. Lett. 114, 035702 (2015).",
                "Korean",
            )
        )
        self.assertFalse(
            untranslated("Monte Carlo methods", "Monte Carlo methods", "Korean")
        )

    def test_names_titles_and_references_are_not_flagged(self):
        authors = "Sehyun Yun, Jihun Ahn, Vikram Thapar, Sangyeop Lee, Sheng Li, and Su-mi Hur"
        self.assertFalse(
            untranslated(authors, authors.replace(", and", " 및"), "Korean")
        )
        ref = (
            "G. Parisi, Statistical Field Theory, Frontiers in Physics Vol. 66 "
            "(Addison-Wesley, Redwood City, 1988), p. 343."
        )
        self.assertFalse(
            untranslated(
                ref,
                "G. Parisi, 『Statistical Field Theory』, 『Frontiers in Physics』 제66권 (Addison-Wesley, Redwood City, 1988), 343쪽.",
                "Korean",
            )
        )

    def test_untranslated_prose_is_flagged(self):
        src = "It is a pleasure to acknowledge stimulating discussions with Daan Frenkel, Giorgio Parisi, and Alan D. Sokal."
        self.assertTrue(untranslated(src, src, "Korean"))

    def test_other_targets_are_not_checked(self):
        self.assertFalse(untranslated(BAD_SRC, BAD_SRC, "French"))

    def test_japanese_and_chinese(self):
        self.assertTrue(untranslated(BAD_SRC, BAD_SRC, "Japanese"))
        self.assertFalse(
            untranslated(
                BAD_SRC,
                "系が状態 \\(i\\) にある確率はベクトルの第 \\(i\\) 成分で与えられ、\\(A\\) は遷移行列である。",
                "Japanese",
            )
        )
        self.assertFalse(
            untranslated(
                BAD_SRC,
                "系统处于状态 \\(i\\) 的概率由向量的第 \\(i\\) 个分量给出，\\(A\\) 是转移矩阵。",
                "Chinese",
            )
        )
