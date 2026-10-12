"""PDF text extraction using PyMuPDF."""

from __future__ import annotations

import contextlib
import re
import statistics

import pymupdf

from . import layout_model


def extract(pdf_bytes: bytes) -> dict:
    """Extract per-page text and basic metadata from a PDF.

    Returns a dict: {"pages": [str, ...], "meta": {"title": str, "num_pages": int}}.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in doc]
        title = _title_from_fonts(doc[0]) if doc.page_count else ""
        if not title:
            meta = doc.metadata or {}
            title = (meta.get("title") or "").strip()
        return {
            "pages": pages,
            "meta": {
                "title": title or "Untitled paper",
                "num_pages": len(pages),
            },
        }
    finally:
        doc.close()


def title_of(pdf_bytes: bytes) -> str:
    """Title of a PDF (for library listings), using the first page's largest font."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return _title_from_fonts(doc[0]) if doc.page_count else ""
    finally:
        doc.close()


def _title_from_fonts(page) -> str:
    """Extract the title as the largest-font text line(s) in the top of page 1.

    This gives properly spaced text (e.g. 'Diffusion Policy: Visuomotor Policy
    Learning via Action Diffusion') instead of the space-stripped metadata title.
    """
    try:
        d = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return ""
    limit_y = page.rect.height * 0.5
    lines = []  # (size, y, text)
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            y = line.get("bbox", [0, 0, 0, 0])[1]
            if not text or y > limit_y:
                continue
            if text.lower().startswith(("arxiv:", "doi:")):
                continue
            size = max((s.get("size", 0) for s in spans), default=0)
            lines.append((size, y, text))
    if not lines:
        return ""
    max_size = max(s for s, _, _ in lines)
    title_lines = [
        t for (s, y, t) in sorted(lines, key=lambda x: x[1]) if s >= max_size - 0.5
    ]
    return " ".join(title_lines)[:300]


def segment(text: str, max_len: int = 600) -> list[str]:
    """Split a page's text into aligned translation units (paragraph-ish).

    PDF text often breaks lines mid-paragraph, so we group into paragraphs on
    blank lines, collapse internal line breaks, then split overly long paragraphs
    on sentence boundaries. Returns a flat list of clean segment strings.
    """
    segments: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())  # collapse newlines + runs of spaces
        if not para:
            continue
        if len(para) <= max_len:
            segments.append(para)
            continue
        # split long paragraph into sentences, re-packing up to max_len
        buf = ""
        for sent in re.split(r"(?<=[.!?])\s+", para):
            if buf and len(buf) + len(sent) + 1 > max_len:
                segments.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            segments.append(buf)
    return segments


try:
    import pysbd

    _SEG = pysbd.Segmenter(language="en", clean=False, char_span=True)
except Exception:  # noqa: BLE001 - pysbd optional; fall back to regex
    _SEG = None

# spaCy is the primary sentence segmenter: its parser keeps inline references like "(Fig 1 a)"
# and lettered enumerations whole, which pysbd mis-splits. Loaded lazily (the model downloads
# once, ~12 MB); if spaCy or the model is unavailable, _split_sentences falls back to pysbd.
_SPACY = None
_SPACY_FAILED = False


def _spacy_segmenter():
    global _SPACY, _SPACY_FAILED
    if _SPACY is not None or _SPACY_FAILED:
        return _SPACY
    try:
        import spacy

        name = "en_core_web_sm"
        try:
            _SPACY = spacy.load(name, exclude=["ner", "lemmatizer"])
        except OSError:  # model not downloaded yet
            from spacy.cli import download

            download(name)
            _SPACY = spacy.load(name, exclude=["ner", "lemmatizer"])
    except Exception:  # noqa: BLE001 — any failure => fall back to pysbd
        _SPACY_FAILED = True
        _SPACY = None
    return _SPACY


def warm_sentence_model() -> bool:
    """Load (and download, on first run) the spaCy model. Called at server startup so the
    first page's layout doesn't pay for it. Returns False if unavailable (pysbd is used)."""
    return _spacy_segmenter() is not None


# Paper abbreviations pysbd misses (merge a sentence that ends with one into the next).
_ABBR_TAIL = re.compile(
    r"\b(Eq|Eqs|Eqn|Fig|Figs|Sec|Secs|Tab|Tabs|Alg|Thm|Lem|Cor|Prop|Def|No|Ref|Refs"
    r"|Ch|App|Appx|vs|cf|al|e\.g|i\.e)\.$",
    re.IGNORECASE,
)


_BULLET_CHARS = "•◦▪▫●○‣⁃∙"

# Enumeration item markers ("a)", "(a)", "(A)", "1)", "(1)", "(i)", "(ii)"...). A sentence
# segmenter only splits a list when its items end in terminal punctuation, so a comma/"and"-joined
# list ("(1) x, (2) y, and (3) z") stays glued and a markerless item ("...landscape c) Diffusion")
# never breaks. We split such runs ourselves, conservatively (see _split_on_markers).
_ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
_ENUM_RE = re.compile(r"(\()?([A-Za-z]{1,4}|\d{1,2})\)")
_ENUM_ABBR = re.compile(
    r"\b(Eq|Eqs|Eqn|Fig|Figs|Sec|Secs|Tab|Tabs|Table|No|Nos|Ref|Refs|Ch|App|Appx"
    r"|Alg|Thm|Lem|Cor|Prop|Def)\.?\s*$",
    re.IGNORECASE,
)

# C0/DEL control characters a PDF uses as large-bracket delimiters inside math (e.g. N\x000,σ²I\x01).
# They are garbage in the text layer, so strip them from every word token before anything else sees
# them — otherwise they leak into a sentence as a fragment like "0 , σ 2 I \x01".
_CTRL_STRIP = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CTRL_STRIP[0x7F] = None


def _split_on_bullets(raw: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    """Split sentence ranges at bullet markers so each list item is its own segment."""
    out: list[tuple[int, int]] = []
    for s, e in raw:
        last = s
        for i in range(s + 1, e):  # not at the very start
            if text[i] in _BULLET_CHARS:
                if text[last:i].strip():
                    out.append((last, i))
                last = i
        if text[last:e].strip():
            out.append((last, e))
    return out


def _starts_lower_frag(text: str, span: tuple[int, int]) -> bool:
    """True when a fragment begins with a lowercase letter — a real sentence never does, so such
    a fragment is an over-split continuation of the one before it (e.g. the segmenter broke a
    sentence at the '...' inside spaced-out inline math 'x k - 1 ... x 0, until ...').
    An enumeration item ('a)', '(i)') starts lowercase but is a real list item, not a
    continuation, so it must NOT be merged back into the sentence before it."""
    frag = text[span[0] : span[1]].lstrip()
    if _ENUM_RE.match(frag):
        return False
    c = frag[:1]
    return c.isalpha() and c.islower() and c.isascii()


def _merge_fragments(
    raw: list[tuple[int, int]], text: str
) -> list[tuple[int, int, str]]:
    """Merge lone section numbers, trailing paper-abbreviations, and lowercase-starting
    over-split fragments (inline math) into the sentence they belong to."""
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(raw):
        s, e = raw[i]
        t = text[s:e].strip()
        while (
            i + 1 < len(raw)
            and (
                re.fullmatch(r"\d+(\.\d+)*\.?", t)
                or _ABBR_TAIL.search(t)
                or len(t) <= 2
                # decimal split from spaced glyphs, e.g. "82 ." + "1%" -> keep together
                or (
                    re.search(r"\d\s*\.$", t)
                    and text[raw[i + 1][0] : raw[i + 1][1]].lstrip()[:1].isdigit()
                )
                # the NEXT fragment starts lowercase -> it continues this sentence
                or _starts_lower_frag(text, raw[i + 1])
            )
        ):
            i += 1
            e = raw[i][1]
            t = text[s:e].strip()
        out.append((s, e, t))
        i += 1
    return out


def _is_enum_label(lab: str) -> bool:
    return lab.isdigit() or (len(lab) == 1 and lab.isalpha()) or lab.lower() in _ROMAN


def _enum_candidates(text: str) -> list[tuple[int, tuple[bool, str]]]:
    """Enumeration item markers in `text` as (start, class), with reference-like matches
    (Eqn.(1), 'Fig 2(a)', 'III-A)', a decimal '0.5)') filtered out. class = (has_paren, kind)."""
    out: list[tuple[int, tuple[bool, str]]] = []
    for m in _ENUM_RE.finditer(text):
        lab = m.group(2)
        if not _is_enum_label(lab):
            continue
        if text[m.end() : m.end() + 1] != " ":  # a real item has text after the marker
            continue
        i = m.start()
        before = text[i - 1] if i > 0 else " "
        if before.isalnum() or before in "-.":  # 2(a), III-A), decimal 0.5)
            continue
        if m.group(1) and _ENUM_ABBR.search(text[:i]):  # Eqn.(1), Fig. (3)
            continue
        out.append((i, (bool(m.group(1)), "digit" if lab.isdigit() else "alpha")))
    return out


def _split_on_markers(raw: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    """Split sentence ranges before each item of a qualifying enumeration run: a run of >=2
    same-class markers introduced by a colon, or any run of >=3. This leaves a lone equation
    number, an 'Eqn.(1)' reference, and a two-part subfigure phrase ('show (a) X and (b) Y')
    intact, while breaking real lists the segmenter glued together."""
    cands = _enum_candidates(text)
    if len(cands) < 2:
        return raw
    splits: set[int] = set()
    i = 0
    while i < len(cands):
        j = i
        while j + 1 < len(cands) and cands[j + 1][1] == cands[i][1]:
            j += 1
        run = cands[i : j + 1]
        first = run[0][0]
        colon = ":" in text[max(0, first - 160) : first]
        if len(run) >= 3 or (len(run) >= 2 and colon):
            splits.update(pos for pos, _ in run)
        i = j + 1
    if not splits:
        return raw
    out: list[tuple[int, int]] = []
    for s, e in raw:
        last = s
        for p in sorted(p for p in splits if s < p < e):
            if text[last:p].strip():
                out.append((last, p))
            last = p
        if text[last:e].strip():
            out.append((last, e))
    return out


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split into sentences, returning (start, end, text). Uses pysbd (handles abbreviations,
    decimals, citations, URLs) with a regex fallback, then merges paper-specific leftovers."""
    raw = None
    nlp = _spacy_segmenter()
    if nlp is not None:
        with contextlib.suppress(Exception):
            doc = nlp(text)
            raw = [(s.start_char, s.end_char) for s in doc.sents if s.text.strip()]
    if raw is None and _SEG is not None:
        with contextlib.suppress(Exception):
            raw = [
                (sp.start, sp.end)
                for sp in _SEG.segment(text)
                if text[sp.start : sp.end].strip()
            ]
    if raw is None:
        raw = [
            (m.start(), m.end())
            for m in re.finditer(r".*?(?:[.!?][\"')\]]?(?=\s|$)|$)", text)
            if m.group().strip()
        ]
    return _merge_fragments(_split_on_markers(_split_on_bullets(raw, text), text), text)


def _keep_sentence(text: str, word_count: int) -> bool:
    """Keep prose and section headings; drop figure/diagram labels and scattered tokens
    (short fragments with no sentence punctuation, e.g. 'Conv1D', 'Cross Attention')."""
    if word_count >= 5:
        return True
    if re.search(r"[.!?][\"')\]]?$", text):  # ends like a sentence
        return True
    if re.search(r"[=∑∫∏√±≈≠≤≥→←⇒∈∉∇∂θελσμβαγδπφψωΩΣ]", text):  # keep equations
        return True
    return bool(
        re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", text)
    )  # section heading "2.2 Training"


def _join_words(widx: list, words: list) -> tuple[str, list]:
    """Build a paragraph's text from its word indices, rejoining words that a line break
    split with a hyphen ('to-' + 'kens' -> 'tokens'). Each word still gets a char range in
    the returned text, so word->sentence mapping (and thus highlighting) stays exact: only
    the hyphen character is dropped. A hyphen is treated as a line-break hyphen when the
    previous token ends '<letter>-' and the next token is a lowercase word on a lower line,
    so real ranges ('2019-') and inline hyphens are left alone."""
    text = ""
    local: list[tuple[int, int, int]] = []
    prev_g: int | None = None
    for g in widx:
        tok = words[g]["t"]
        pt = words[prev_g]["t"] if prev_g is not None else ""
        if (
            prev_g is not None
            and len(pt) >= 2
            and pt.endswith("-")
            and pt[-2].isalpha()
            and tok[:1].isalpha()
            and tok[:1].islower()
            and words[g]["y0"] > words[prev_g]["y0"] + 2  # next word on a lower line
        ):
            text = text[:-2]  # drop trailing 'hyphen + space' -> contiguous join
            ps, pe, pg = local[-1]
            local[-1] = (ps, pe - 1, pg)
        start = len(text)
        text += tok
        local.append((start, len(text), g))
        text += " "
        prev_g = g
    return text, local


def _words_with_style(page) -> list[dict]:
    """Words with geometry AND font size in reading order, via get_text('dict').

    Word boxes are approximated by splitting each styled span proportionally — exact enough
    for highlighting, and the per-word font size feeds the paragraph font sizing in the panel."""
    out: list[dict] = []
    d = page.get_text("dict")
    for bno, block in enumerate(d.get("blocks", [])):
        if block.get("type") != 0:  # skip images
            continue
        for lno, line in enumerate(block.get("lines", [])):
            for span in line.get("spans", []):
                stext = span.get("text", "")
                if not stext.strip():
                    continue
                size = span.get("size", 0.0)
                sx0, sy0, sx1, sy1 = span.get("bbox", (0, 0, 0, 0))
                cw = (sx1 - sx0) / len(stext) if stext else 0
                pos = sx0
                for tok in re.findall(r"\S+\s*", stext):
                    wx1 = pos + cw * len(tok)
                    t = tok.strip().translate(_CTRL_STRIP)
                    if t:
                        out.append(
                            {
                                "x0": round(pos, 2),
                                "y0": round(sy0, 2),
                                "x1": round(min(wx1, sx1), 2),
                                "y1": round(sy1, 2),
                                "t": t,
                                "size": round(size, 1),
                                "bno": bno,
                                "lno": lno,
                            }
                        )
                    pos = wx1
    return out


def _norm_header(text: str) -> str:
    """Normalize a line for running-header matching: drop digits (page numbers vary),
    lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text)).strip().lower()


def running_headers(pdf_bytes: bytes) -> set[str]:
    """Normalized text of lines that repeat at the top or bottom across pages — i.e. the
    running header/footer (paper title, 'A PREPRINT', venue, page furniture). Computed once
    per document; the caller caches it and passes it to layout() to drop these lines."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        n = doc.page_count
        if n < 3:
            return set()
        top: dict[str, int] = {}
        bot: dict[str, int] = {}
        sample = list(range(min(n, 12)))
        for pno in sample:
            page = doc[pno]
            h = page.rect.height
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    txt = "".join(
                        s.get("text", "") for s in line.get("spans", [])
                    ).strip()
                    norm = _norm_header(txt)
                    if len(norm) < 4:
                        continue
                    y0, y1 = (
                        line.get("bbox", (0, 0, 0, 0))[1],
                        line.get("bbox", (0, 0, 0, 0))[3],
                    )
                    if y0 < h * 0.09:
                        top[norm] = top.get(norm, 0) + 1
                    elif y1 > h * 0.93:
                        bot[norm] = bot.get(norm, 0) + 1
        # Headers often alternate (author names on even pages, title on odd), so each
        # appears on only ~half the pages; require a third of the sample, not a half.
        thr = max(2, round(len(sample) / 3))
        return {t for t, c in top.items() if c >= thr} | {
            t for t, c in bot.items() if c >= thr
        }
    finally:
        doc.close()


# "References"/"Bibliography" are always a real section heading (never a footnote), so they
# are matched at any readable size; the weaker markers below need body size to avoid firing on
# a first-page funding/ethics footnote.
_REF_RE = re.compile(r"^(references?|bibliography)\b", re.IGNORECASE)
_TAIL_RE = re.compile(
    r"^(acknowledge?ments?|acknowledgements?|funding|"
    r"declarations?|data availability|author contributions?|"
    r"conflicts? of interest|competing interests?|ethics statement)\b",
    re.IGNORECASE,
)
# An appendix / supplementary section that follows the references — real content, kept.
# Matches "Appendix", "Supplementary ...", a lone section letter ("A", "B"), or "A Title".
_APPENDIX_RE = re.compile(r"^(appendix\b|supplementary\b|[A-Z]\.?\s+[A-Z]|[A-Z]$)")

# Position in the document as (page_index, y); compares in reading order.
Pos = tuple[int, float]
SkipRange = tuple[Pos, Pos | None]  # (start, end-exclusive); end None = to document end


def end_matter_range(
    pdf_bytes: bytes, headers: set[str] | None = None
) -> SkipRange | None:
    """The span of end matter to skip in translation: from the first References/
    Acknowledgements/Funding heading to the start of the appendix that follows it (exclusive),
    or to the document end when no appendix follows. Returns (start, end) as (page, y)
    positions, or None if there is no such section. An appendix after the references IS kept —
    it is real content. Page 0 is excluded, and a heading must be at least body size (~10pt),
    so an author-funding footnote never triggers it. Running-header lines (e.g. 'A PREPRINT')
    are ignored so they are not mistaken for an appendix start that would leak the references."""
    hdrs = headers or set()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        n = doc.page_count

        def headings(pno):
            for block in doc[pno].get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    t = "".join(s.get("text", "") for s in spans).strip()
                    if (
                        _norm_header(t) in hdrs
                    ):  # running header/footer, not a section title
                        continue
                    sz = max((s.get("size", 0.0) for s in spans), default=0.0)
                    yield t, sz, line.get("bbox", (0, 0, 0, 0))[1]

        start: Pos | None = None
        for pno in range(1, n):
            ys = [
                y
                for t, sz, y in headings(pno)
                if t
                and len(t.split()) <= 6
                and ((sz >= 7 and _REF_RE.match(t)) or (sz >= 9 and _TAIL_RE.match(t)))
            ]
            if ys:
                start = (pno, min(ys))
                break
        if start is None:
            return None

        # Resume point: the first appendix/supplementary heading at/after the references. Take
        # the topmost such heading on its page (block order is not strictly top-to-bottom).
        for pno in range(start[0], n):
            ys = [
                y
                for t, sz, y in headings(pno)
                if sz >= 11
                and len(t.split()) <= 7
                and _APPENDIX_RE.match(t)
                and not (pno == start[0] and y < start[1])
            ]
            if ys:
                return (start, (pno, min(ys)))
        return (start, None)
    finally:
        doc.close()


def _in_rects(cx: float, cy: float, rects: list) -> bool:
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects)


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?][\"')\]]?$", text.strip()))


def _starts_lower(text: str) -> bool:
    t = text.strip()
    return bool(t) and t[0].isalpha() and t[0].islower()


def _tail_unfinished(page: dict) -> bool:
    """True when a page's last sentence is a prose fragment that wraps to the next page. It
    must be a real mid-sentence break: not a heading or equation, no terminal punctuation, and
    ending in a lowercase letter or comma. This excludes equations (end in a symbol/number),
    captions and sentences closing on a citation bracket or a colon — which are complete even
    without a period — so their following page is never stitched onto them."""
    if not page["sentences"]:
        return False
    last = page["sentences"][-1]
    if last.get("heading") or last.get("equation"):
        return False
    t = last["text"].rstrip()
    if not t or _ends_sentence(t):
        return False
    return t[-1].islower() or t[-1] == ","


def _continuation_idx(page: dict, max_scan: int = 16) -> int | None:
    """Index of the sentence that continues a wrap from the previous page: the first prose
    sentence that starts lowercase (a mid-sentence continuation). Float captions a page drops at
    the top (a whole figure/table block, e.g. "Figure 7. ... Columns 1-4 show ...") start
    uppercase, so they are skipped and the continuation after them is still found — the scan is
    wide enough to clear such a block. But a heading STOPS the scan (returns None): a new section
    cannot be the continuation of the previous page's dangling sentence. Equations are skipped.
    Returns None if there is no such sentence."""
    for j, s in enumerate(page["sentences"][:max_scan]):
        if s.get("heading"):
            return None  # a new section starts before any continuation → no stitch
        if s.get("equation"):
            continue
        if _starts_lower(s["text"]):
            return j
    return None


def layout(
    pdf_bytes: bytes,
    page_index: int,
    headers: set[str] | None = None,
    skip: SkipRange | None = None,
) -> dict:
    """Per-page layout with cross-page sentence stitching. A sentence that wraps across the
    page break is kept whole on the page where it STARTS: that page absorbs the continuation
    fragment from the next page (recording its geometry under `words_next` so a highlight can
    span both pages), and the next page drops that fragment so it is not shown or translated
    twice. The continuation is found by the same rule from both sides (the previous page's
    tail is unfinished, and this page's first lowercase-starting sentence is the continuation),
    so the two pages always agree — even when a float caption sits between them."""
    core = _page_layout(pdf_bytes, page_index, headers, skip)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        n = doc.page_count
    finally:
        doc.close()
    # Forward runs BEFORE backward, so it tests this page's ORIGINAL tail — the same view the
    # next page's backward pass uses — keeping the two pages' decisions consistent. If our
    # tail is unfinished, absorb the next page's continuation fragment so the sentence reads
    # whole here.
    if page_index + 1 < n and _tail_unfinished(core):
        nxt = _page_layout(pdf_bytes, page_index + 1, headers, skip)
        j = _continuation_idx(nxt)
        if j is not None:
            frag = nxt["sentences"][j]
            last = core["sentences"][-1]
            tail = last["text"]
            # Rejoin a word the page break split with a hyphen ("applicabil-" + "ity").
            if re.search(r"[a-z]-$", tail):
                last["text"] = tail[:-1] + frag["text"]
            else:
                last["text"] = (tail + " " + frag["text"]).strip()
            last["cont_next_page"] = page_index + 1
            last["words_next"] = [
                {k: nxt["words"][i][k] for k in ("x0", "y0", "x1", "y1")}
                for i in frag["words"]
            ]
    # Backward: if the previous page's (original) tail is unfinished, our continuation sentence
    # belongs to that page — drop it here so it is not shown or translated twice.
    if page_index > 0 and core["sentences"]:
        prev = _page_layout(pdf_bytes, page_index - 1, headers, skip)
        if _tail_unfinished(prev):
            j = _continuation_idx(core)
            if j is not None:
                del core["sentences"][j]
    return core


def _page_layout(
    pdf_bytes: bytes,
    page_index: int,
    headers: set[str] | None = None,
    skip: SkipRange | None = None,
) -> dict:
    """Word-level geometry + sentence grouping for one page (no cross-page stitching).

    Returns {width, height, words:[{x0,y0,x1,y1,t}], sentences:[{text, words:[idx,...]}]}.
    Coordinates are in PDF points (top-left origin), matching a pdf.js viewport scaled by s
    (left=x0*s, top=y0*s). Sentence boundaries fall between words, so assigning each word to
    the sentence its char-range overlaps is exact — no line-level bleed.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        pw, ph = page.rect.width, page.rect.height

        # End matter (references/acknowledgements/funding) is not translated. `skip` is a
        # (start, end) range in reading order; a page wholly inside it is empty, and on its
        # boundary pages the words inside the range are dropped. An appendix after the range
        # is outside it and stays translatable.
        def _skipped(y0: float) -> bool:
            if skip is None:
                return False
            (sp, sy), end = skip
            if not (page_index > sp or (page_index == sp and y0 >= sy - 2)):
                return False
            if end is None:
                return True
            ep, ey = end
            return page_index < ep or (page_index == ep and y0 < ey)

        if skip is not None:
            (sp, _sy), end = skip
            fully_inside = sp < page_index and (end is None or page_index < end[0])
            if fully_inside:
                return {
                    "width": round(pw, 2),
                    "height": round(ph, 2),
                    "words": [],
                    "sentences": [],
                }

        ml, mr, mt, mb = (
            44,
            20,
            38,
            32,
        )  # margins: arXiv stamp, page numbers, running heads

        # The layout model is required (enforced at startup): it types every region
        # (title/body/figure/table/formula/furniture) from the rendered page. The text and word
        # coordinates still come from PyMuPDF, so highlighting is unchanged — the model only says
        # WHAT each area is. No heuristic region detectors: they overfit one paper's layout and
        # can emit a full-width false-positive band that swallows real body text and a heading.
        yregions = layout_model.detect_page(page) or []

        def _rects(roles):
            return [
                pymupdf.Rect(r["x0"], r["y0"], r["x1"], r["y1"])
                for r in yregions
                if r["role"] in roles
            ]

        regions = _rects({"figure", "table", "furniture"})
        keep_rects = _rects({"body", "title", "caption"})
        title_rects = _rects({"title"})
        formula_rects = _rects({"formula"})

        def _drop(w) -> bool:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2
            if (
                w["x1"] <= ml
                or w["x0"] >= pw - mr
                or w["y1"] <= mt
                or w["y0"] >= ph - mb
            ):
                return True
            if _skipped(w["y0"]):
                return True
            # A region the model typed as text/caption wins over an overlapping figure/table box:
            # tables and figures sometimes bleed into the adjacent column at their edges.
            if _in_rects(cx, cy, keep_rects):
                return False
            return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in regions)

        raw = [w for w in _words_with_style(page) if not _drop(w)]
        # On page 1, drop everything above the Abstract (title, authors, affiliations).
        if page_index == 0:
            cut = next(
                (w["y0"] for w in raw if w["t"].lower().startswith("abstract")),
                ph * 0.22,
            )
            raw = [w for w in raw if w["y1"] > cut]

        words = [
            {"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"], "t": w["t"]}
            for w in raw
        ]

        # 1) Group words into lines (per-line font size and geometry).
        lines = []
        cur_line = None
        for gi, w in enumerate(raw):
            key = (w["bno"], w["lno"])
            if cur_line is None or cur_line["key"] != key:
                if cur_line:
                    lines.append(cur_line)
                cur_line = {
                    "key": key,
                    "widx": [],
                    "sizes": [],
                    "y0": [],
                    "y1": [],
                    "x0": [],
                }
            cur_line["widx"].append(gi)
            cur_line["sizes"].append(w["size"])
            cur_line["y0"].append(w["y0"])
            cur_line["y1"].append(w["y1"])
            cur_line["x0"].append(w["x0"])
        if cur_line:
            lines.append(cur_line)
        for ln in lines:
            ln["text"] = " ".join(words[g]["t"] for g in ln["widx"])
            ln["size"] = statistics.median(ln["sizes"]) if ln["sizes"] else 0
            ln["top"], ln["bottom"], ln["left"] = (
                min(ln["y0"]),
                max(ln["y1"]),
                min(ln["x0"]),
            )
            ln["h"] = ln["bottom"] - ln["top"]
            ln["cx"] = (min(ln["x0"]) + max(words[g]["x1"] for g in ln["widx"])) / 2
            ln["cy"] = (ln["top"] + ln["bottom"]) / 2
            # A line is a display-equation line when its center is inside a model formula box.
            # Consecutive such lines (one box spans the whole equation) group into one segment.
            ln["math"] = _in_rects(ln["cx"], ln["cy"], formula_rects)

        # Rejoin fragments the PDF split WITHIN one visual line. Inline math — e.g. "(0, σ²I)" in
        # "… and N(0, σ²I) is Gaussian noise added …" — is often typeset as its own text block
        # beside the prose, and a display equation is likewise split into adjacent spans. Two
        # reading-order-adjacent lines on the same baseline, separated by only a small horizontal
        # gap, are the same visual line, so merge them; otherwise that inline block breaks the
        # sentence (and its highlight). The gap guard keeps a two-column row apart — its halves are
        # far apart, and reading order never puts two columns' lines adjacent at the same y.
        def _lright(ln):
            return max(words[g]["x1"] for g in ln["widx"])

        def _recompute(ln):
            ln["text"] = " ".join(words[g]["t"] for g in ln["widx"])
            ln["size"] = statistics.median(ln["sizes"]) if ln["sizes"] else 0
            ln["top"], ln["bottom"], ln["left"] = (
                min(ln["y0"]),
                max(ln["y1"]),
                min(ln["x0"]),
            )
            ln["h"] = ln["bottom"] - ln["top"]
            ln["cx"] = (min(ln["x0"]) + _lright(ln)) / 2
            ln["cy"] = (ln["top"] + ln["bottom"]) / 2
            ln["math"] = _in_rects(ln["cx"], ln["cy"], formula_rects)

        merged_lines = []
        for ln in lines:
            if merged_lines:
                prev = merged_lines[-1]
                size = prev["size"] or ln["size"] or 10
                gap = ln["left"] - _lright(prev)
                same_baseline = (
                    abs(ln["cy"] - prev["cy"]) <= max(prev["h"], ln["h"]) * 0.6
                )
                if same_baseline and -size <= gap <= size * 1.8:
                    prev["widx"].extend(ln["widx"])
                    prev["sizes"].extend(ln["sizes"])
                    prev["y0"].extend(ln["y0"])
                    prev["y1"].extend(ln["y1"])
                    prev["x0"].extend(ln["x0"])
                    _recompute(prev)
                    continue
            merged_lines.append(ln)
        lines = merged_lines

        # Drop running headers/footers (repeated top/bottom lines across pages).
        if headers:
            lines = [
                ln
                for ln in lines
                if not (
                    _norm_header(ln["text"]) in headers
                    and (ln["top"] < ph * 0.12 or ln["bottom"] > ph * 0.90)
                )
            ]

        # A line is a heading when its center is inside a model title box. A font/number heuristic
        # supplement was tried and measured against arXiv .tex: it dropped precision ~10 points
        # (flagging numbered algorithm steps and short lines) while barely moving recall and NOT
        # recovering the one pathological old-journal paper the model misses — so pure title-box
        # detection is kept. A title box may omit a detached section number, so the lone-number
        # merge below still pairs a standalone "2.1" with the title that follows.
        for ln in lines:
            ln["heading"] = _in_rects(ln["cx"], ln["cy"], title_rects)

        # Merge a lone section-number line ("1", "2.2") into the title that follows, so it reads
        # "1 Introduction" / "2.1 Denoising ...". Many papers typeset the number as a separate
        # element, so the title line carries no number and may not look like a heading on its
        # own (italic, only slightly larger than body, or outside the model's title box). A
        # standalone number is a reliable section marker, so we pair it with the next title-like
        # line (short, capitalized, not a full sentence) and mark the result a heading. Runs in
        # BOTH the heuristic and the model path.
        merged = []
        k = 0
        while k < len(lines):
            ln = lines[k]
            nxt = lines[k + 1] if k + 1 < len(lines) else None
            if nxt is not None and re.fullmatch(r"\d+(\.\d+)*\.?", ln["text"].strip()):
                nt = nxt["text"].strip()
                title_like = (
                    len(nxt["widx"]) <= 12
                    and nt[:1].isupper()
                    and not re.search(r"[.!?]$", nt)
                    and not re.fullmatch(r"\d+(\.\d+)*\.?", nt)
                    and not nxt.get("math")
                )
                if nxt["heading"] or title_like:
                    nxt["widx"] = ln["widx"] + nxt["widx"]
                    nxt["text"] = ln["text"] + " " + nt
                    nxt["heading"] = True
                    k += 1
                    continue
            merged.append(ln)
            k += 1
        lines = merged

        # 2) Reconstruct paragraphs. Lines in the SAME block always join (a wrapped
        #    paragraph). Across blocks they join only when the previous line did not end a
        #    sentence AND the next line is reading-adjacent (same column just below, or the
        #    top of the next column) — so a sentence can span columns without gluing
        #    far-apart blocks (e.g. keywords and a figure caption).
        paras = []
        cur = None
        prev = None
        prev_ended = True
        for ln in lines:
            in_eq = cur is not None and cur.get("equation")
            is_eq_line = bool(ln.get("math"))
            if is_eq_line and not ln["heading"]:
                # display-equation line: keep whole; consecutive equation lines merge.
                if in_eq:
                    cur["widx"].extend(ln["widx"])
                else:
                    if cur:
                        paras.append(cur)
                    cur = {
                        "widx": list(ln["widx"]),
                        "size": ln["size"],
                        "heading": False,
                        "equation": True,
                    }
                prev, prev_ended = ln, True
                continue
            if ln["heading"]:
                if cur:
                    paras.append(cur)
                paras.append(
                    {"widx": list(ln["widx"]), "size": ln["size"], "heading": True}
                )
                cur, prev, prev_ended = None, None, True
                continue
            if cur is not None and cur.get("equation"):
                paras.append(cur)  # a prose line ends the equation paragraph
                cur = None
            cont = False
            if cur is not None and prev is not None:
                if ln["key"][0] == prev["key"][0]:  # same block
                    cont = True
                elif not prev_ended:
                    gap = ln["top"] - prev["bottom"]
                    same_col = (
                        abs(ln["left"] - prev["left"]) < 40
                        and -5 <= gap <= prev["h"] * 2.2
                    )
                    col_jump = (
                        ln["left"] > prev["left"] + 120 and ln["top"] < prev["top"]
                    )
                    cont = same_col or col_jump
            if cont:
                cur["widx"].extend(ln["widx"])
            else:
                if cur:
                    paras.append(cur)
                cur = {"widx": list(ln["widx"]), "size": ln["size"], "heading": False}
            prev_ended = bool(re.search(r"[.!?][\"')\]]?$", ln["text"].strip()))
            prev = ln
        if cur:
            paras.append(cur)

        # 3) Split each paragraph into sentences (equations are kept whole).
        sentences = []
        for pid, para in enumerate(paras):
            if para.get("equation"):
                st = " ".join(words[g]["t"] for g in para["widx"]).strip()
                if st:
                    heights = [words[g]["y1"] - words[g]["y0"] for g in para["widx"]]
                    sentences.append(
                        {
                            "text": st,
                            "words": list(para["widx"]),
                            "para": pid,
                            "size": round(statistics.median(heights), 1)
                            if heights
                            else 0,
                            "heading": False,
                            "equation": True,
                        }
                    )
                continue
            text, local = _join_words(para["widx"], words)
            for s, e, st in _split_sentences(text):
                idxs = [g for (a, b, g) in local if a < e and b > s]
                # headings/labels are always kept (shown as titles); body uses the filter
                if idxs and (para["heading"] or _keep_sentence(st, len(idxs))):
                    heights = [words[g]["y1"] - words[g]["y0"] for g in idxs]
                    size = round(statistics.median(heights), 1) if heights else 0
                    sentences.append(
                        {
                            "text": st,
                            "words": idxs,
                            "para": pid,
                            "size": size,
                            "heading": para["heading"],
                        }
                    )

        return {
            "width": round(page.rect.width, 2),
            "height": round(page.rect.height, 2),
            "words": words,
            "sentences": sentences,
        }
    finally:
        doc.close()


def full_text(pages: list[str], max_chars: int = 200_000) -> str:
    """Join page texts into a single document string, capped to stay sane."""
    joined = "\n\n".join(f"[Page {i + 1}]\n{t}" for i, t in enumerate(pages))
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n\n[...truncated...]"
    return joined


def brief_source(pages: list[str], max_chars: int = 6000) -> str:
    """The front matter used to build a translation glossary: title, abstract, and the start
    of the introduction all live in the first pages, where a paper defines its abbreviations
    and key terms. Feeding only this (not the whole paper) keeps the glossary pass fast."""
    joined = "\n\n".join(pages[:2])
    return joined[:max_chars]


def crop_region_png(
    pdf_bytes: bytes,
    page_index: int,
    bbox: tuple[float, float, float, float],
    dpi: int = 220,
    pad: float = 5.0,
) -> bytes:
    """Render one page region (bbox in PDF points) to PNG bytes — the image a vision call
    reads to transcribe a display equation to LaTeX."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        x0, y0, x1, y1 = bbox
        clip = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
        return page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")
    finally:
        doc.close()
