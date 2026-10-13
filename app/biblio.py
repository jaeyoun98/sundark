"""Bibliographic Record lookup for doi:/arxiv: Paper IDs (CrossRef, arXiv).

Only the identifier is sent; no user data. Failures return None and are not cached by the
caller, so the next open retries."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT_S = 6
_UA = "sundark (local research paper reader)"
_ATOM = "{http://www.w3.org/2005/Atom}"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read()


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _crossref(doi: str) -> dict:
    msg = json.loads(
        _get("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/"))
    )["message"]
    authors = [
        _squash(f"{a.get('given', '')} {a.get('family', '')}")
        for a in msg.get("author", [])
    ]
    parts = (msg.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "title": _squash((msg.get("title") or [""])[0]),
        "authors": [a for a in authors if a],
        "year": parts[0][0],
        "source": "crossref",
    }


def _arxiv(aid: str) -> dict:
    root = ET.fromstring(
        _get("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(aid))
    )
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        raise ValueError("arXiv returned no entry")
    published = entry.findtext(f"{_ATOM}published") or ""
    return {
        "title": _squash(entry.findtext(f"{_ATOM}title") or ""),
        "authors": [
            _squash(a.findtext(f"{_ATOM}name") or "")
            for a in entry.findall(f"{_ATOM}author")
        ],
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "source": "arxiv",
    }


async def lookup(pid: str) -> dict | None:
    scheme, _, value = pid.partition(":")
    fetch = {"doi": _crossref, "arxiv": _arxiv}.get(scheme)
    if fetch is None or not value:
        return None
    try:
        record = await asyncio.to_thread(fetch, value)
    except Exception:  # noqa: BLE001 — best-effort: any failure means "no record yet"
        return None
    return record if record["title"] else None


def _norm(text: str) -> str:
    # NFKC folds ligatures (ﬁ, ﬀ, ﬃ) and full-width forms the PDF text layer often keeps.
    return unicodedata.normalize("NFKC", text or "").lower()


def _words(text: str) -> list[str]:
    return [
        w for w in re.findall(r"\w+", _norm(text)) if len(w) > (2 if w.isascii() else 1)
    ]


def title_matches(title: str, page_text: str) -> bool:
    """True when most of the record's title words appear on the PDF's first page. A DOI read
    from the PDF can belong to a cited work; its title will not be on this first page.
    Non-Latin words (Korean, CJK) are matched as substrings, since they are not always
    split by spaces the same way in the PDF text layer."""
    words = _words(title)
    if not words:
        return False
    page_words, page = set(_words(page_text)), _norm(page_text)
    hits = sum(w in page_words or (not w.isascii() and w in page) for w in words)
    return hits / len(words) >= 0.6


def byline(record: dict | None) -> str:
    if not record:
        return ""
    authors = record.get("authors") or []
    who = f"{authors[0]} et al." if len(authors) > 2 else ", ".join(authors)
    year = record.get("year")
    return f"{who} ({year})" if who and year else who or (str(year) if year else "")
