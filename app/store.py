"""Local filesystem storage for uploaded PDFs and cached AI results.

Layout (all under ./data, gitignored):
  data/pdfs/<doc_id>.pdf        original upload
  data/docs/<doc_id>.json       extracted {"pages": [...], "meta": {...}}
  data/cache/<doc_id>/<key>.json cached AI outputs (summary, translations)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
PDFS = DATA / "pdfs"
DOCS = DATA / "docs"
CACHE = DATA / "cache"

for _d in (PDFS, DOCS, CACHE):
    _d.mkdir(parents=True, exist_ok=True)


def doc_id_for(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()[:16]


def save_pdf(doc_id: str, pdf_bytes: bytes) -> None:
    (PDFS / f"{doc_id}.pdf").write_bytes(pdf_bytes)


def pdf_path(doc_id: str) -> Path:
    return PDFS / f"{doc_id}.pdf"


def save_doc(doc_id: str, extracted: dict) -> None:
    (DOCS / f"{doc_id}.json").write_text(json.dumps(extracted), encoding="utf-8")


def load_doc(doc_id: str) -> dict | None:
    p = DOCS / f"{doc_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def cache_get(doc_id: str, key: str) -> dict | None:
    p = CACHE / doc_id / f"{key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def cache_put(doc_id: str, key: str, value: dict) -> None:
    d = CACHE / doc_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
