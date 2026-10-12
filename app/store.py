"""Local filesystem storage for uploaded PDFs and cached AI results.

Layout (all under ./data, gitignored):
  data/pdfs/<doc_id>.pdf        original upload
  data/docs/<doc_id>.json       extracted {"pages": [...], "meta": {...}}
  data/cache/<doc_id>/<key>.json cached AI outputs (summary, translations)
  data/papers/<key>.json         Bibliographic Record per Paper ID (key = hash of the Paper ID)
  data/chats/<key>.json          Chat History per Paper ID
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
PDFS = DATA / "pdfs"
DOCS = DATA / "docs"
CACHE = DATA / "cache"
CHATS = DATA / "chats"
PAPERS = DATA / "papers"

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


def _key(pid: str) -> str:
    """Filesystem-safe name for a Paper ID (DOIs may contain '/', '<', ';', ...)."""
    return hashlib.sha256(pid.encode("utf-8")).hexdigest()[:16]


def paper_get(pid: str) -> dict | None:
    try:
        return json.loads((PAPERS / f"{_key(pid)}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


def paper_put(pid: str, record: dict) -> None:
    PAPERS.mkdir(parents=True, exist_ok=True)
    (PAPERS / f"{_key(pid)}.json").write_text(
        json.dumps({"paper_id": pid, **record}, ensure_ascii=False), "utf-8"
    )


def paper_cache_id(pid: str) -> str:
    """A cache_get/cache_put id for caches that belong to the Paper, not to one PDF file."""
    return f"paper_{_key(pid)}"


def chat_get(pid: str) -> list[dict]:
    try:
        turns = json.loads((CHATS / f"{_key(pid)}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return turns if isinstance(turns, list) else []


def chat_put(pid: str, turns: list[dict]) -> None:
    CHATS.mkdir(parents=True, exist_ok=True)
    (CHATS / f"{_key(pid)}.json").write_text(
        json.dumps(turns, ensure_ascii=False), "utf-8"
    )


def chat_clear(pid: str) -> None:
    (CHATS / f"{_key(pid)}.json").unlink(missing_ok=True)


def chat_move(old_pid: str, new_pid: str) -> None:
    """Carry a Paper's chat over when its Paper ID changes, never overwriting."""
    old, new = CHATS / f"{_key(old_pid)}.json", CHATS / f"{_key(new_pid)}.json"
    if old.exists() and old != new and not new.exists():
        old.replace(new)


def chat_migrate(doc_id: str, pid: str) -> None:
    """Move a chat saved per doc_id (before Paper IDs) to its Paper, never overwriting."""
    old = CHATS / f"{doc_id}.json"
    new = CHATS / f"{_key(pid)}.json"
    if old.exists() and old != new and not new.exists():
        old.replace(new)
