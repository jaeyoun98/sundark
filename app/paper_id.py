"""Paper ID: which scholarly work a PDF holds (ADR 0001).

Priority: DOI, else arXiv ID, else Zotero item key, else the File Fingerprint (doc_id).
Identifiers come from the PDF metadata and the first two pages only, because later pages
are full of cited works' DOIs."""

from __future__ import annotations

import re

import pymupdf

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ARXIV = re.compile(r"arxiv(?:\.org/abs/|:)\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_MAX_DOIS_PER_PAGE = 3
_ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)


def _clean_doi(doi: str) -> str:
    return doi.rstrip(".,;:)]}'\"").lower()


def identifiers(pdf_bytes: bytes) -> dict:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        meta = " ".join(str(v) for v in (doc.metadata or {}).values() if v)
        pages = [doc[i].get_text() for i in range(min(2, len(doc)))]
    finally:
        doc.close()
    doi = None
    for text in (meta, *pages):
        found = [_clean_doi(m.group(1)) for m in _DOI.finditer(text)]
        # A page listing many DOIs is a table of contents or reference list; its first DOI
        # usually belongs to another paper, so skip it.
        if found and len(set(found)) < _MAX_DOIS_PER_PAGE:
            doi = found[0]
            break
    arxiv = None
    for text in (meta, *pages):
        m = _ARXIV.search(text)
        if m:
            arxiv = m.group(1)
            break
    return {"doi": doi, "arxiv": arxiv}


def paper_id(
    *,
    fingerprint: str,
    doi: str | None = None,
    arxiv: str | None = None,
    zotero_key: str | None = None,
) -> str:
    if doi:
        m = _ARXIV_DOI.fullmatch(doi)
        return f"arxiv:{m.group(1)}" if m else f"doi:{doi.lower()}"
    if arxiv:
        return f"arxiv:{arxiv}"
    if zotero_key:
        return f"zotero:{zotero_key}"
    return f"file:{fingerprint}"
