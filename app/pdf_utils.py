"""PDF text extraction using PyMuPDF."""

from __future__ import annotations

import contextlib
import re
import statistics

import pymupdf


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
    title_lines = [t for (s, y, t) in sorted(lines, key=lambda x: x[1]) if s >= max_size - 0.5]
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

# Paper abbreviations pysbd misses (merge a sentence that ends with one into the next).
_ABBR_TAIL = re.compile(
    r"\b(Eq|Eqs|Eqn|Fig|Figs|Sec|Secs|Tab|Tabs|Alg|Thm|Lem|Cor|Prop|Def|No|Ref|Refs"
    r"|Ch|App|Appx|vs|cf|al|e\.g|i\.e)\.$",
    re.IGNORECASE,
)


_BULLET_CHARS = "•◦▪▫●○‣⁃∙"


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


def _merge_fragments(raw: list[tuple[int, int]], text: str) -> list[tuple[int, int, str]]:
    """Merge lone section numbers and trailing paper-abbreviations into the next sentence."""
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(raw):
        s, e = raw[i]
        t = text[s:e].strip()
        while i + 1 < len(raw) and (
            re.fullmatch(r"\d+(\.\d+)*\.?", t)
            or _ABBR_TAIL.search(t)
            or len(t) <= 2
            # decimal split from spaced glyphs, e.g. "82 ." + "1%" -> keep together
            or (re.search(r"\d\s*\.$", t) and text[raw[i + 1][0]:raw[i + 1][1]].lstrip()[:1].isdigit())
        ):
            i += 1
            e = raw[i][1]
            t = text[s:e].strip()
        out.append((s, e, t))
        i += 1
    return out


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split into sentences, returning (start, end, text). Uses pysbd (handles abbreviations,
    decimals, citations, URLs) with a regex fallback, then merges paper-specific leftovers."""
    raw = None
    if _SEG is not None:
        with contextlib.suppress(Exception):
            raw = [(sp.start, sp.end) for sp in _SEG.segment(text) if text[sp.start:sp.end].strip()]
    if raw is None:
        raw = [
            (m.start(), m.end())
            for m in re.finditer(r".*?(?:[.!?][\"')\]]?(?=\s|$)|$)", text)
            if m.group().strip()
        ]
    return _merge_fragments(_split_on_bullets(raw, text), text)


def _keep_sentence(text: str, word_count: int) -> bool:
    """Keep prose and section headings; drop figure/diagram labels and scattered tokens
    (short fragments with no sentence punctuation, e.g. 'Conv1D', 'Cross Attention')."""
    if word_count >= 5:
        return True
    if re.search(r"[.!?][\"')\]]?$", text):  # ends like a sentence
        return True
    if re.search(r"[=∑∫∏√±≈≠≤≥→←⇒∈∉∇∂θελσμβαγδπφψωΩΣ]", text):  # keep equations
        return True
    return bool(re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", text))  # section heading "2.2 Training"


def _merge_rects(rects: list, pad: float = 6.0) -> list:
    """Union overlapping rectangles (expanded by pad) into clusters."""
    boxes = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list = []
        for b in boxes:
            grown = pymupdf.Rect(b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad)
            for o in out:
                if o.intersects(grown):
                    o |= b
                    changed = True
                    break
            else:
                out.append(pymupdf.Rect(b))
        boxes = out
    return boxes


def _figure_regions(page) -> list:
    """Figure/diagram regions: raster images + large vector-drawing clusters.
    Text whose center falls inside these is figure-internal and is not translated."""
    regions: list = []
    with contextlib.suppress(Exception):
        for img in page.get_images(full=True):
            regions.extend(pymupdf.Rect(r) for r in page.get_image_rects(img[0]))
    with contextlib.suppress(Exception):
        pw, ph = page.rect.width, page.rect.height
        draw = [pymupdf.Rect(d["rect"]) for d in page.get_drawings() if d.get("rect")]
        draw = [r for r in draw if r.width > 10 and r.height > 10 and r.width < pw * 0.98]
        for c in _merge_rects(draw, pad=8):
            if c.width > 40 and c.height > 40 and c.width * c.height > pw * ph * 0.03:
                regions.append(c)
    return regions


_LABEL_RE = re.compile(
    r"^(abstract|keywords?|index terms|introduction|conclusions?|discussion|references|"
    r"acknowledge?ments?|related work|background|appendix|methods?|results?|contents)\b",
    re.IGNORECASE,
)


def _words_with_style(page) -> list[dict]:
    """Words with geometry AND font style (size, bold) in reading order, via get_text('dict').

    Word boxes are approximated by splitting each styled span proportionally — exact enough
    for highlighting, and it gives us the per-word font size/bold needed to detect headings
    and run-in labels (Abstract, Keywords, bold section titles) across paper formats."""
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
                font = span.get("font", "")
                bold = bool(span.get("flags", 0) & 16) or "bold" in font.lower() or "black" in font.lower()
                sx0, sy0, sx1, sy1 = span.get("bbox", (0, 0, 0, 0))
                cw = (sx1 - sx0) / len(stext) if stext else 0
                pos = sx0
                for tok in re.findall(r"\S+\s*", stext):
                    wx1 = pos + cw * len(tok)
                    t = tok.strip()
                    if t:
                        out.append({
                            "x0": round(pos, 2), "y0": round(sy0, 2),
                            "x1": round(min(wx1, sx1), 2), "y1": round(sy1, 2),
                            "t": t, "bold": bold, "size": round(size, 1), "bno": bno, "lno": lno,
                        })
                    pos = wx1
    return out


def layout(pdf_bytes: bytes, page_index: int) -> dict:
    """Word-level geometry + sentence grouping for one page.

    Returns {width, height, words:[{x0,y0,x1,y1,t}], sentences:[{text, words:[idx,...]}]}.
    Coordinates are in PDF points (top-left origin), matching a pdf.js viewport scaled by s
    (left=x0*s, top=y0*s). Sentence boundaries fall between words, so assigning each word to
    the sentence its char-range overlaps is exact — no line-level bleed.
    """
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        pw, ph = page.rect.width, page.rect.height
        ml, mr, mt, mb = 44, 20, 38, 32  # margins: arXiv stamp, page numbers, running heads
        regions = _figure_regions(page)

        def _drop(w) -> bool:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2
            if w["x1"] <= ml or w["x0"] >= pw - mr or w["y1"] <= mt or w["y0"] >= ph - mb:
                return True
            return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in regions)

        raw = [w for w in _words_with_style(page) if not _drop(w)]
        # On page 1, drop everything above the Abstract (title, authors, affiliations).
        if page_index == 0:
            cut = next((w["y0"] for w in raw if w["t"].lower().startswith("abstract")), ph * 0.22)
            raw = [w for w in raw if w["y1"] > cut]

        words = [{"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"], "t": w["t"]} for w in raw]

        # 1) Group words into lines (per-line font size, bold ratio, and geometry).
        lines = []
        cur_line = None
        for gi, w in enumerate(raw):
            key = (w["bno"], w["lno"])
            if cur_line is None or cur_line["key"] != key:
                if cur_line:
                    lines.append(cur_line)
                cur_line = {"key": key, "widx": [], "sizes": [], "bolds": [], "y0": [], "y1": [], "x0": []}
            cur_line["widx"].append(gi)
            cur_line["sizes"].append(w["size"])
            cur_line["bolds"].append(w["bold"])
            cur_line["y0"].append(w["y0"])
            cur_line["y1"].append(w["y1"])
            cur_line["x0"].append(w["x0"])
        if cur_line:
            lines.append(cur_line)
        for ln in lines:
            ln["text"] = " ".join(words[g]["t"] for g in ln["widx"])
            ln["size"] = statistics.median(ln["sizes"]) if ln["sizes"] else 0
            ln["bold"] = sum(ln["bolds"]) / len(ln["bolds"]) if ln["bolds"] else 0
            ln["top"], ln["bottom"], ln["left"] = min(ln["y0"]), max(ln["y1"]), min(ln["x0"])
            ln["h"] = ln["bottom"] - ln["top"]

        body_size = statistics.median([ln["size"] for ln in lines if ln["size"] > 0] or [0])

        def _heading_kind(ln):
            t, wc = ln["text"].strip(), len(ln["widx"])
            if wc > 16 or (t and t[0] in _BULLET_CHARS):  # bullet list items are not headings
                return None
            if re.match(r"^\d+(\.\d+)*\s+[A-Z]", t):          # numbered section
                return "strong"
            if _LABEL_RE.match(t) and wc <= 6:                # Abstract / Keywords / ...
                return "strong"
            if body_size > 0 and ln["size"] >= body_size * 1.15 and wc <= 8:  # larger AND short
                return "strong"
            if ln["bold"] >= 0.6 and not re.search(r"[.!?]$", t):  # short bold line
                return "weak"
            return None

        kinds = [_heading_kind(ln) for ln in lines]

        def _is_body(j) -> bool:
            return 0 <= j < len(lines) and kinds[j] is None and len(lines[j]["widx"]) >= 8

        # A short bold line counts as a heading only when a body line follows — this keeps
        # real section titles but rejects scattered figure/diagram labels (which sit among
        # other short labels, not before a paragraph).
        for idx, ln in enumerate(lines):
            ln["heading"] = kinds[idx] == "strong" or (kinds[idx] == "weak" and _is_body(idx + 1))

        # Merge a lone section-number heading ("1", "2.2") into the following heading line,
        # so it reads "1 Introduction" instead of two fragments.
        merged = []
        k = 0
        while k < len(lines):
            ln = lines[k]
            if (ln["heading"] and re.fullmatch(r"\d+(\.\d+)*\.?", ln["text"].strip())
                    and k + 1 < len(lines) and lines[k + 1]["heading"]):
                lines[k + 1]["widx"] = ln["widx"] + lines[k + 1]["widx"]
                lines[k + 1]["text"] = ln["text"] + " " + lines[k + 1]["text"]
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
            if ln["heading"]:
                if cur:
                    paras.append(cur)
                paras.append({"widx": list(ln["widx"]), "size": ln["size"], "heading": True})
                cur, prev, prev_ended = None, None, True
                continue
            cont = False
            if cur is not None and prev is not None:
                if ln["key"][0] == prev["key"][0]:  # same block
                    cont = True
                elif not prev_ended:
                    gap = ln["top"] - prev["bottom"]
                    same_col = abs(ln["left"] - prev["left"]) < 40 and -5 <= gap <= prev["h"] * 2.2
                    col_jump = ln["left"] > prev["left"] + 120 and ln["top"] < prev["top"]
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

        # 3) Split each paragraph into sentences.
        sentences = []
        for pid, para in enumerate(paras):
            text = ""
            local: list[tuple[int, int, int]] = []
            for g in para["widx"]:
                start = len(text)
                text += words[g]["t"]
                local.append((start, len(text), g))
                text += " "
            for s, e, st in _split_sentences(text):
                idxs = [g for (a, b, g) in local if a < e and b > s]
                # headings/labels are always kept (shown as titles); body uses the filter
                if idxs and (para["heading"] or _keep_sentence(st, len(idxs))):
                    heights = [words[g]["y1"] - words[g]["y0"] for g in idxs]
                    size = round(statistics.median(heights), 1) if heights else 0
                    sentences.append({
                        "text": st, "words": idxs, "para": pid,
                        "size": size, "heading": para["heading"],
                    })

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
