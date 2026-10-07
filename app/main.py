"""Paper Reader — FastAPI backend.

Personal, local tool. Serves the static reader UI and a small JSON API that
calls Claude (via the subscription CLI) for summary, translation, and Q&A.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import claude, pdf_utils, store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Folder the app scans for papers. Drop PDFs here (or set PAPERS_DIR to another path).
PAPERS_DIR = Path(os.environ.get("PAPERS_DIR", ROOT / "papers")).resolve()
PAPERS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Paper Reader")

# In-memory chat sessions: doc_id -> claude session_id (reset on restart; fine for a personal tool).
_chat_sessions: dict[str, str] = {}


def _ingest(data: bytes) -> dict:
    """Store + extract a PDF (idempotent by content hash). Returns summary meta."""
    doc_id = store.doc_id_for(data)
    extracted = store.load_doc(doc_id)
    if extracted is None:
        extracted = pdf_utils.extract(data)  # may raise; caller converts to HTTP error
        store.save_pdf(doc_id, data)
        store.save_doc(doc_id, extracted)
    return {
        "doc_id": doc_id,
        "title": extracted["meta"]["title"],
        "num_pages": extracted["meta"]["num_pages"],
    }


_TITLES_FILE = store.DATA / "titles.json"
try:
    _titles: dict[str, str] = json.loads(_TITLES_FILE.read_text("utf-8"))
except Exception:  # noqa: BLE001
    _titles = {}


def _paper_title(path: Path, rel: str) -> str:
    st = path.stat()
    key = f"{rel}:{int(st.st_mtime)}:{st.st_size}"
    if key in _titles:
        return _titles[key]
    try:
        title = pdf_utils.title_of(path.read_bytes())
    except Exception:  # noqa: BLE001
        title = ""
    _titles[key] = title
    try:
        _TITLES_FILE.write_text(json.dumps(_titles, ensure_ascii=False), "utf-8")
    except OSError:
        pass  # non-fatal: title cache is best-effort
    return title


@app.get("/api/papers")
async def papers():
    """List PDF files in the papers folder, newest first, titled when possible."""
    items = []
    for p in PAPERS_DIR.rglob("*.pdf"):
        if not p.is_file():
            continue
        st = p.stat()
        items.append(
            {
                "rel": str(p.relative_to(PAPERS_DIR)),
                "name": _paper_title(p, str(p.relative_to(PAPERS_DIR))) or p.stem,
                "file": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"dir": str(PAPERS_DIR), "papers": items}


class OpenReq(BaseModel):
    rel: str


@app.post("/api/open")
async def open_paper(req: OpenReq):
    """Open a paper from the papers folder by its relative path."""
    path = (PAPERS_DIR / req.rel).resolve()
    if not path.is_relative_to(PAPERS_DIR):
        raise HTTPException(400, "Invalid path")
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise HTTPException(404, "Paper not found")
    try:
        return _ingest(path.read_bytes())
    except Exception as e:
        raise HTTPException(400, f"Could not read PDF: {e}") from e


@app.get("/api/layout/{doc_id}/{page}")
async def layout(doc_id: str, page: int):
    """Word-level geometry + sentence grouping for one page (cached)."""
    doc = _require_doc(doc_id)
    if not (0 <= page < doc["meta"]["num_pages"]):
        raise HTTPException(400, "page out of range")
    key = f"layout_{page}"
    cached = store.cache_get(doc_id, key)
    if cached:
        return cached
    pdf_path = store.pdf_path(doc_id)
    if not pdf_path.is_file():
        raise HTTPException(404, "PDF missing")
    result = pdf_utils.layout(pdf_path.read_bytes(), page)
    store.cache_put(doc_id, key, result)
    return result


@app.get("/api/pdf/{doc_id}")
async def get_pdf(doc_id: str):
    path = store.pdf_path(doc_id)
    if not path.is_file():
        raise HTTPException(404, "Unknown doc_id")
    return FileResponse(path, media_type="application/pdf")


@app.post("/api/upload")
async def upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a .pdf file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        return _ingest(data)
    except Exception as e:
        raise HTTPException(400, f"Could not read PDF: {e}") from e


class SummarizeReq(BaseModel):
    doc_id: str
    model: str = claude.DEFAULT_MODEL


@app.post("/api/summarize")
async def summarize(req: SummarizeReq):
    doc = _require_doc(req.doc_id)
    model = claude.normalize_model(req.model)
    key = f"summary_{model}"
    cached = store.cache_get(req.doc_id, key)
    if cached:
        return cached
    text = pdf_utils.full_text(doc["pages"])
    summary = await claude.summarize(text, model=model)
    result = {"summary": summary}
    store.cache_put(req.doc_id, key, result)
    return result


class AnatomyReq(BaseModel):
    doc_id: str
    model: str = claude.DEFAULT_MODEL
    force: bool = False


def _verify_card_quotes(card: dict, paper_text: str) -> None:
    """Annotate each point in a card with whether its quote is grounded in the paper."""
    if not isinstance(card, dict):
        return
    for pt in card.get("points", []) or []:
        if isinstance(pt, dict):
            pt["verified"] = claude.verify_quote(pt.get("quote") or "", paper_text)


@app.post("/api/anatomy")
async def anatomy(req: AnatomyReq):
    doc = _require_doc(req.doc_id)
    model = claude.normalize_model(req.model)
    key = f"anatomy_{model}"
    if not req.force:
        cached = store.cache_get(req.doc_id, key)
        if cached:
            return cached

    text = pdf_utils.full_text(doc["pages"])
    try:
        result = await claude.anatomy(text, model=model)
    except claude.ClaudeError as e:
        raise HTTPException(502, f"분석 실패: {e}") from e

    # Grounding: flag each point's quote as verified/unverified against the paper text.
    for name in ("problem", "gap", "claim", "method", "limitations"):
        _verify_card_quotes(result.get(name, {}), text)
    for sub in (result.get("evidence") or {}).values():
        _verify_card_quotes(sub, text)

    store.cache_put(req.doc_id, key, result)
    return result


class TranslateReq(BaseModel):
    doc_id: str
    page: int  # 0-indexed
    target_lang: str = "Korean"
    model: str = claude.DEFAULT_MODEL
    segments: list[str] | None = None  # client-supplied segments (from the PDF text layer)
    force: bool = False  # re-translate, ignoring any cached result


@app.post("/api/translate")
async def translate(req: TranslateReq):
    doc = _require_doc(req.doc_id)
    pages = doc["pages"]
    if not (0 <= req.page < len(pages)):
        raise HTTPException(400, "page out of range")

    model = claude.normalize_model(req.model)
    if req.segments:
        segs = [s for s in req.segments if s.strip()]
    else:
        segs = pdf_utils.segment(pages[req.page].strip())

    # Cache key includes a signature of the exact segments, so a change in how the
    # page is split (different count/content) never returns a mismatched cached result.
    sig = hashlib.sha1("\x1f".join(segs).encode("utf-8")).hexdigest()[:10]
    key = f"tr_{req.target_lang}_{model}_{req.page}_{sig}"
    cached = store.cache_get(req.doc_id, key)
    if cached:
        return cached

    if not segs:
        result = {"segments": [], "translation": ""}
        store.cache_put(req.doc_id, key, result)
        return result

    source = "\n\n".join(segs)
    tr = await claude.translate_segments(segs, req.target_lang, model=model)
    if tr is None:
        # Alignment failed — fall back to one whole-page translation.
        whole = await claude.translate(source, req.target_lang, model=model)
        result = {"segments": [{"src": source, "tr": whole}], "translation": whole}
    else:
        pairs = [{"src": s, "tr": t} for s, t in zip(segs, tr)]
        result = {"segments": pairs, "translation": "\n\n".join(tr)}
    store.cache_put(req.doc_id, key, result)
    return result


@app.post("/api/translate_stream")
async def translate_stream(req: TranslateReq):
    """Progressive translation: streams one NDJSON line {"i":k,"tr":"..."} per segment
    as the model produces it, so sentences appear from the top instead of all at once."""
    doc = _require_doc(req.doc_id)
    pages = doc["pages"]
    if not (0 <= req.page < len(pages)):
        raise HTTPException(400, "page out of range")

    model = claude.normalize_model(req.model)
    segs = [s for s in (req.segments or []) if s.strip()] or pdf_utils.segment(pages[req.page].strip())
    sig = hashlib.sha1("\x1f".join(segs).encode("utf-8")).hexdigest()[:10]
    key = f"tr_{req.target_lang}_{model}_{req.page}_{sig}"

    async def gen():
        if not req.force:
            cached = store.cache_get(req.doc_id, key)
            if cached:
                for k, seg in enumerate(cached["segments"]):
                    yield json.dumps({"i": k, "tr": seg["tr"]}, ensure_ascii=False) + "\n"
                return
        if not segs:
            store.cache_put(req.doc_id, key, {"segments": [], "translation": ""})
            return
        collected: list[str] = []
        async for tr in claude.translate_stream(segs, req.target_lang, model=model):
            if len(collected) >= len(segs):
                break
            k = len(collected)
            collected.append(tr)
            yield json.dumps({"i": k, "tr": tr}, ensure_ascii=False) + "\n"
        pairs = [{"src": segs[k], "tr": collected[k] if k < len(collected) else ""} for k in range(len(segs))]
        store.cache_put(req.doc_id, key, {"segments": pairs, "translation": "\n\n".join(collected)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class ExplainReq(BaseModel):
    doc_id: str
    text: str
    model: str = claude.DEFAULT_MODEL


@app.post("/api/explain")
async def explain(req: ExplainReq):
    _require_doc(req.doc_id)
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "No text selected")
    answer = await claude.explain(text[:4000], model=claude.normalize_model(req.model))
    return {"explanation": answer}


class ChatReq(BaseModel):
    doc_id: str
    question: str
    model: str = claude.DEFAULT_MODEL


@app.post("/api/chat")
async def chat(req: ChatReq):
    doc = _require_doc(req.doc_id)
    model = claude.normalize_model(req.model)
    resume = _chat_sessions.get(req.doc_id)
    paper_text = None if resume else pdf_utils.full_text(doc["pages"])
    out = await claude.chat(req.question, paper_text=paper_text, resume=resume, model=model)
    if out.get("session_id"):
        _chat_sessions[req.doc_id] = out["session_id"]
    return {"answer": out["text"]}


def _require_doc(doc_id: str) -> dict:
    doc = store.load_doc(doc_id)
    if doc is None:
        raise HTTPException(404, "Unknown doc_id — upload the PDF again")
    return doc


# --- static UI (mounted last so /api/* wins) ---
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
