"""Measure the PDF-only parser against LaTeX-source ground truth.

For each paper we have both the PDF and its arXiv .tex source. The .tex gives the TRUE
section structure, so we can score the PDF parser objectively instead of by eye:
headings found vs. missed, references detected, etc. Runtime is still PDF-only — this
harness only runs during development to tune and regression-test the heuristics.

Usage (run from the project root):
    python -I eval/tex_eval.py --pdf-dir <dir with <id>.pdf> --tex-dir <dir with <id>/ sources>

The .tex sources are untrusted: this script only READS them, never imports or executes
anything from those directories.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# -I ignores PYTHONPATH, so add the project root explicitly to import the app package.
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import pymupdf  # noqa: E402

from app import pdf_utils  # noqa: E402

# ---------- LaTeX ground-truth extraction ----------


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_comments(s: str) -> str:
    # Drop % to end of line, but keep an escaped \%.
    return re.sub(r"(?<!\\)%.*", "", s)


def find_main_tex(tex_dir: Path) -> Path | None:
    texs = list(tex_dir.rglob("*.tex"))
    if not texs:
        return None
    with_doc = [t for t in texs if "\\begin{document}" in _read(t)]
    pool = with_doc or texs

    def score(t: Path) -> tuple:
        s = _read(t)
        name = t.name.lower()
        # The real root makes the title/abstract and pulls in the body; an appendix or
        # supplement file can also be a standalone document, so rank it below.
        return (
            "\\documentclass" in s,
            any(k in s for k in ("\\maketitle", "\\begin{abstract}", "\\title{")),
            not any(k in name for k in ("appendix", "supp", "suppl")),
            s.count("\\input") + s.count("\\include"),
            len(s),
        )

    return max(pool, key=score)


def flatten(main: Path, depth: int = 0, seen: set | None = None) -> str:
    """Inline \\input / \\include so multi-file papers are seen whole."""
    if depth > 8:
        return ""
    seen = seen if seen is not None else set()
    if main in seen:
        return ""
    seen.add(main)
    text = _strip_comments(_read(main))

    def repl(m: re.Match) -> str:
        name = m.group(1).strip()
        cand = main.parent / name
        for p in (cand, cand.with_suffix(".tex"), Path(str(cand) + ".tex")):
            if p.is_file():
                return flatten(p, depth + 1, seen)
        return ""

    return re.sub(r"\\(?:input|include)\{([^}]+)\}", repl, text)


def _clean_tex(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+\*?\s*", " ", s)  # drop control words
    s = re.sub(r"[{}$\\]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _balanced(s: str, start: int) -> tuple[str, int]:
    """Read a {...} group starting at index of '{'; return (inner, index after '}')."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i + 1
    return "", len(s)


def tex_sections(src: str) -> list[str]:
    """Titles of \\section / \\subsection / \\subsubsection (not starred macro defs)."""
    out: list[str] = []
    for m in re.finditer(r"\\(?:sub)*section\*?\s*\{", src):
        inner, _ = _balanced(src, m.end() - 1)
        title = _clean_tex(inner)
        # Skip macro definitions like \section{\@ifstar...} and empty titles.
        if title and not inner.lstrip().startswith("\\") and len(title) >= 2:
            out.append(title)
    return out


def has_bibliography(src: str) -> bool:
    return bool(
        re.search(r"\\(bibliography\b|begin\{thebibliography\}|printbibliography)", src)
    )


# ---------- PDF-side extraction ----------


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"^\d+(\.\d+)*\.?\s*", "", s)  # strip a leading section number
    # Drop everything but letters/digits: this also collapses the inter-letter spaces that
    # small-caps section titles ("I NTRODUCTION") leave in the PDF text layer.
    return re.sub(r"[^a-z0-9]+", "", s).strip()


def pdf_signals(pdf_bytes: bytes) -> dict:
    headers = pdf_utils.running_headers(pdf_bytes)
    skip = pdf_utils.end_matter_range(pdf_bytes, headers=headers)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    headings: list[str] = []
    try:
        for pg in range(doc.page_count):
            lay = pdf_utils.layout(pdf_bytes, pg, headers=headers, skip=skip)
            for s in lay["sentences"]:
                if s.get("heading"):
                    headings.append(s["text"])
    finally:
        doc.close()
    return {"headings": headings, "skip": skip}


def _matches(title: str, headings_norm: list[str]) -> bool:
    t = _norm(title)
    if not t:
        return False
    return any(t == h or t in h or h in t for h in headings_norm if h)


# ---------- main ----------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--tex-dir", required=True)
    ap.add_argument("--show-missed", action="store_true")
    args = ap.parse_args()
    pdf_dir, tex_dir = Path(args.pdf_dir), Path(args.tex_dir)

    tot_titles = tot_found = tot_detected = tot_matched_det = 0
    rows = []
    for sub in sorted(p for p in tex_dir.iterdir() if p.is_dir()):
        pid = sub.name
        pdfs = list(pdf_dir.glob(f"{pid}.pdf")) or list(pdf_dir.glob(f"*{pid}*.pdf"))
        if not pdfs:
            continue
        main_tex = find_main_tex(sub)
        if not main_tex:
            continue
        src = flatten(main_tex)
        titles = tex_sections(src)
        if not titles:
            continue
        print(f"... processing {pid}", file=sys.stderr, flush=True)
        sig = pdf_signals(pdfs[0].read_bytes())
        hnorm = [_norm(h) for h in sig["headings"]]
        found = [t for t in titles if _matches(t, hnorm)]
        # precision: detected headings that match some true title
        tnorm = [_norm(t) for t in titles]
        matched_det = sum(
            1 for h in hnorm if h and any(h == t or t in h or h in t for t in tnorm)
        )
        recall = len(found) / len(titles)
        prec = matched_det / len(hnorm) if hnorm else 0.0
        bib = has_bibliography(src)
        skip_ok = (sig["skip"] is not None) if bib else True
        rows.append((pid, len(titles), recall, len(hnorm), prec, bib, skip_ok))
        tot_titles += len(titles)
        tot_found += len(found)
        tot_detected += len(hnorm)
        tot_matched_det += matched_det
        if args.show_missed:
            missed = [t for t in titles if not _matches(t, hnorm)]
            if missed:
                print(f"  [{pid}] missed headings: {missed[:8]}")

    print(
        f"\n{'paper':16} {'titles':>6} {'recall':>7} {'detect':>6} {'prec':>6} bib skip"
    )
    for pid, nt, rec, nd, pr, bib, sk in rows:
        flag = "" if (not bib or sk) else "  <-- refs not detected"
        print(
            f"{pid:16} {nt:6d} {rec:7.0%} {nd:6d} {pr:6.0%} "
            f"{'Y' if bib else '-'}   {'Y' if sk else 'N'}{flag}"
        )
    print(
        f"\nAGGREGATE heading recall={tot_found}/{tot_titles}"
        f"={tot_found / max(tot_titles, 1):.1%}  "
        f"precision={tot_matched_det}/{tot_detected}"
        f"={tot_matched_det / max(tot_detected, 1):.1%}"
    )


if __name__ == "__main__":
    main()
