"""Paper Reader — FastAPI backend.

Personal, local tool. Serves the static reader UI and a small JSON API that
calls Claude (via the subscription CLI) for summary, translation, and Q&A.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, backends, layout_model, pdf_utils, settings, store
from .errors import AIError, RateLimitError

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Folder the app scans for papers. Drop PDFs here (or set PAPERS_DIR to another path).
PAPERS_DIR = Path(os.environ.get("PAPERS_DIR", ROOT / "papers")).resolve()
PAPERS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Paper Reader")


@app.exception_handler(AIError)
async def _ai_error(_request: Request, exc: AIError):
    limited = isinstance(exc, RateLimitError)
    return JSONResponse(
        status_code=429 if limited else 502,
        content={"detail": str(exc), "rate_limited": limited},
    )


@app.on_event("startup")
async def _warm_layout_model() -> None:
    """BLOCK startup until the layout model is loaded (it downloads on first run). Uvicorn only
    reports "Application startup complete" — and only then accepts requests — once this returns,
    so the homepage is reachable only after the model is ready and the first paper opens without
    waiting on it. The layout model is REQUIRED: region typing has no heuristic fallback, so if
    it can't load we fail startup with a clear message instead of serving a broken parser."""
    print("Loading models (first run downloads them)…", flush=True)
    ready = await asyncio.to_thread(layout_model.available)
    if not ready:
        raise RuntimeError(
            "Layout model (DocLayout-YOLO) failed to load. It is required — the parser has no "
            "heuristic fallback. Check the torch/torchvision install (both from the SAME index) "
            "and that the model can download, then restart. See AGENTS.md."
        )
    print("Layout model ready.", flush=True)
    seg = await asyncio.to_thread(pdf_utils.warm_sentence_model)
    print(
        "Sentence model ready." if seg else "Sentence model unavailable — using pysbd.",
        flush=True,
    )


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
        return _schedule_prewarm(_ingest(path.read_bytes()))
    except Exception as e:
        raise HTTPException(400, f"Could not read PDF: {e}") from e


# Layout (which now runs the YOLO model) is heavy and synchronous. Run it one page at a time
# so the CPU is not thrashed by parallel inference, and always off the event loop (via a
# thread) so one page's layout never blocks the server from serving the PDF or other requests.
_layout_lock = asyncio.Lock()


# Bump when layout() changes what it emits, so cached pages from an older parser are bypassed
# instead of silently reused. The translation cache keys on a segment signature, so it follows
# automatically; only the layout cache needs this tag. (v2: pure-YOLO region typing. v3:
# cross-page stitch skips figure/table caption blocks. v4: merge lowercase-starting over-split
# fragments from spaced inline math. A hybrid-heading attempt was tried and reverted — it hurt
# precision on the .tex eval — so heading detection stays pure title-box. v5: strip C0/DEL control
# characters (PDF math bracket delimiters) from word tokens. v6: rejoin same-baseline line
# fragments the PDF split around inline math.)
_LAYOUT_VERSION = 6


def _page_layout_cached(doc_id: str, page: int) -> dict:
    """Layout for one page (cached). Running headers and the end-matter range are detected
    once per document and cached, then the per-page layout is cached too. Synchronous and
    heavy — call it through `_layout_async`, never directly in a request handler."""
    pdf_path = store.pdf_path(doc_id)
    if not pdf_path.is_file():
        raise HTTPException(404, "PDF missing")
    cached = store.cache_get(doc_id, f"layout_v{_LAYOUT_VERSION}_{page}")
    if cached:
        return cached
    pdf_bytes = pdf_path.read_bytes()
    hc = store.cache_get(doc_id, "headers")
    if hc is None:
        hc = {"set": sorted(pdf_utils.running_headers(pdf_bytes))}
        store.cache_put(doc_id, "headers", hc)
    sc = store.cache_get(doc_id, "skip")
    if sc is None:
        sc = {"range": pdf_utils.end_matter_range(pdf_bytes, headers=set(hc["set"]))}
        store.cache_put(doc_id, "skip", sc)
    result = pdf_utils.layout(pdf_bytes, page, headers=set(hc["set"]), skip=sc["range"])
    store.cache_put(doc_id, f"layout_v{_LAYOUT_VERSION}_{page}", result)
    return result


async def _layout_async(doc_id: str, page: int) -> dict:
    """Cached fast-path (no lock), else compute one-at-a-time in a worker thread."""
    cached = store.cache_get(doc_id, f"layout_v{_LAYOUT_VERSION}_{page}")
    if cached is not None:
        return cached
    async with _layout_lock:
        cached = store.cache_get(
            doc_id, f"layout_v{_LAYOUT_VERSION}_{page}"
        )  # another task may have filled it
        if cached is not None:
            return cached
        return await asyncio.to_thread(_page_layout_cached, doc_id, page)


# Layout pre-warm progress, so the UI can show a loading bar: doc_id -> pages done; a doc_id
# in _prewarm_done means the whole sweep finished (even if some pages failed).
_prewarm_progress: dict[str, int] = {}
_prewarm_done: set[str] = set()


async def _prewarm_layout(doc_id: str, num_pages: int) -> None:
    """Compute every page's layout from the top, in the background, so scrolling and
    translation hit the cache instead of waiting on the model. Tracks progress for /api/status."""
    _prewarm_done.discard(doc_id)
    _prewarm_progress[doc_id] = 0
    for p in range(num_pages):
        try:
            await _layout_async(doc_id, p)
        except Exception:  # noqa: BLE001 — best-effort warm-up
            pass
        _prewarm_progress[doc_id] = p + 1
    _prewarm_done.add(doc_id)


@app.get("/api/status/{doc_id}")
async def status(doc_id: str):
    """Layout pre-warm progress for the loading bar: {done, num_pages, finished}."""
    doc = _require_doc(doc_id)
    n = doc["meta"]["num_pages"]
    return {
        "done": min(_prewarm_progress.get(doc_id, 0), n),
        "num_pages": n,
        "finished": doc_id in _prewarm_done,
    }


_bg_tasks: set = set()  # strong refs so fire-and-forget tasks are not GC'd mid-run


def _schedule_prewarm(meta: dict) -> dict:
    task = asyncio.create_task(_prewarm_layout(meta["doc_id"], meta["num_pages"]))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return meta


@app.get("/api/layout/{doc_id}/{page}")
async def layout(doc_id: str, page: int):
    """Word-level geometry + sentence grouping for one page (cached)."""
    doc = _require_doc(doc_id)
    if not (0 <= page < doc["meta"]["num_pages"]):
        raise HTTPException(400, "page out of range")
    return await _layout_async(doc_id, page)


@app.get("/api/math/{doc_id}/{page}")
async def math(doc_id: str, page: int):
    """LaTeX for each display equation on a page, read from the rendered crop by a vision call.
    Compiled PDFs scramble 2D math in the text layer, so we transcribe the image instead.
    Returns {"equations": [{"i": segment_index, "latex": "..."}]} (cached per page/model)."""
    doc = _require_doc(doc_id)
    if not (0 <= page < doc["meta"]["num_pages"]):
        raise HTTPException(400, "page out of range")
    # Keyed by _LAYOUT_VERSION too: the result stores segment indices, which shift when layout()
    # changes, so an older cache would map equations onto the wrong segments.
    key = f"math_v{_LAYOUT_VERSION}_{ai.cache_tag('equation')}_{page}"
    cached = store.cache_get(doc_id, key)
    if cached:
        return cached
    lay = await _layout_async(doc_id, page)
    pdf_bytes = store.pdf_path(doc_id).read_bytes()
    words = lay["words"]
    out = []
    for i, seg in enumerate(lay["sentences"]):
        if not seg.get("equation") or not seg.get("words"):
            continue
        idxs = seg["words"]
        bbox = (
            min(words[j]["x0"] for j in idxs),
            min(words[j]["y0"] for j in idxs),
            max(words[j]["x1"] for j in idxs),
            max(words[j]["y1"] for j in idxs),
        )
        png = pdf_utils.crop_region_png(pdf_bytes, page, bbox)
        try:
            latex = await ai.equation_latex(png)
        except RateLimitError:
            raise
        except AIError:
            latex = ""  # best-effort: keep the original text if a crop can't be read
        if latex:
            out.append({"i": i, "latex": latex})
    result = {"equations": out}
    store.cache_put(doc_id, key, result)
    return result


@app.get("/api/pdf/{doc_id}")
async def get_pdf(doc_id: str):
    path = store.pdf_path(doc_id)
    if not path.is_file():
        raise HTTPException(404, "Unknown doc_id")
    return FileResponse(path, media_type="application/pdf")


def _save_to_library(filename: str, data: bytes) -> None:
    """Also keep an uploaded PDF in the papers folder so it shows up in the Library and can be
    reopened later. Best-effort: a failure here must not fail the upload (the doc is already
    ingested and opens from data/). The name is reduced to a basename, so no path can escape
    PAPERS_DIR."""
    name = Path(filename or "upload.pdf").name
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    dest = PAPERS_DIR / name
    try:
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists() and dest.read_bytes() != data:
            dest = (
                PAPERS_DIR / f"{stem}-{i}{suffix}"
            )  # keep a same-named, different file
            i += 1
        if not dest.exists():
            dest.write_bytes(data)
    except OSError:
        pass


@app.post("/api/upload")
async def upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a .pdf file")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        meta = _ingest(data)
    except Exception as e:
        raise HTTPException(400, f"Could not read PDF: {e}") from e
    _save_to_library(file.filename, data)
    return _schedule_prewarm(meta)


class SummarizeReq(BaseModel):
    doc_id: str


@app.post("/api/summarize")
async def summarize(req: SummarizeReq):
    doc = _require_doc(req.doc_id)
    key = f"summary_{ai.cache_tag('summary')}"
    cached = store.cache_get(req.doc_id, key)
    if cached:
        return cached
    text = pdf_utils.full_text(doc["pages"])
    summary = await ai.summarize(text)
    result = {"summary": summary}
    store.cache_put(req.doc_id, key, result)
    return result


class AnatomyReq(BaseModel):
    doc_id: str
    force: bool = False


def _verify_card_quotes(card: dict, paper_text: str) -> None:
    """Annotate each point in a card with whether its quote is grounded in the paper."""
    if not isinstance(card, dict):
        return
    for pt in card.get("points", []) or []:
        if isinstance(pt, dict):
            pt["verified"] = ai.verify_quote(pt.get("quote") or "", paper_text)


@app.post("/api/anatomy")
async def anatomy(req: AnatomyReq):
    doc = _require_doc(req.doc_id)
    key = f"anatomy_{ai.cache_tag('anatomy')}"
    if not req.force:
        cached = store.cache_get(req.doc_id, key)
        if cached:
            return cached

    text = pdf_utils.full_text(doc["pages"])
    try:
        result = await ai.anatomy(text)
    except RateLimitError:
        raise
    except AIError as e:
        raise HTTPException(502, f"분석 실패: {e}") from e

    # Grounding: flag each point's quote as verified/unverified against the paper text.
    for name in ("problem", "gap", "claim", "method", "limitations"):
        _verify_card_quotes(result.get(name, {}), text)
    for sub in (result.get("evidence") or {}).values():
        _verify_card_quotes(sub, text)

    store.cache_put(req.doc_id, key, result)
    return result


# --- Review export -------------------------------------------------------------------------
# A one-click "memory note": synthesize a Korean review from the materials already gathered for
# a paper (summary + anatomy + chat), crop only the figures the note references, and hand back a
# Hugo page bundle (<slug>/index.md + <slug>/<id>.png) as a zip.

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Markdown image whose target is one of our figure ids, e.g. ![caption](fig-p3-1.png).
_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\((fig|tbl)-p\d+-\d+\.png\)")
_IMG_ID_RE = re.compile(r"!\[[^\]]*\]\(((?:fig|tbl)-p\d+-\d+)\.png\)")


def _slug(title: str, fallback: str) -> str:
    """ASCII kebab-case slug for the bundle folder; `fallback` (the doc id) when nothing is left
    (e.g. a title with no ASCII letters)."""
    s = _SLUG_RE.sub("-", (title or "").lower()).strip("-")
    return s or fallback


def _referenced_ids(body: str) -> list[str]:
    """Figure ids the review body actually links to, in first-seen order."""
    seen: list[str] = []
    for m in _IMG_ID_RE.finditer(body):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _yaml_value(v) -> str:
    """Serialize a frontmatter scalar/list as YAML. JSON flow syntax is valid YAML, so quoting
    strings and lists through json keeps them safe without a YAML dependency."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def _frontmatter(rv: dict, *, date: str, has_math: bool) -> str:
    """Hugo frontmatter block. `date`, `draft`, `math`, `arxiv`, `link` are set deterministically;
    the rest come from the model's review object."""
    fields = [
        ("title", rv.get("title") or rv.get("paper_title") or "Untitled"),
        ("date", date),
        ("draft", True),
        ("paper_title", rv.get("paper_title") or ""),
        ("authors", rv.get("authors") or []),
        ("venue", rv.get("venue") or ""),
        ("year", rv.get("year") if isinstance(rv.get("year"), int) else None),
        ("arxiv", ""),
        ("link", ""),
        ("tags", rv.get("tags") or []),
        ("summary", rv.get("summary") or ""),
        ("math", has_math),
    ]
    lines = ["---"]
    for k, v in fields:
        # `date` is an unquoted RFC3339 timestamp, not a YAML-quoted string.
        lines.append(f"date: {date}" if k == "date" else f"{k}: {_yaml_value(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


@app.post("/api/review/{doc_id}")
async def review_export(doc_id: str):
    """Generate the review note and return it as a zipped Hugo page bundle."""
    doc = _require_doc(doc_id)
    pages = doc["pages"]
    text = pdf_utils.full_text(pages)

    # Materials: summary and anatomy are generated (and cached) on demand if not present yet.
    # A single material failing (e.g. anatomy returns unparseable JSON) must not sink the whole
    # export — the review can still be written from the rest. A rate limit still propagates.
    skey = f"summary_{ai.cache_tag('summary')}"
    summ = store.cache_get(doc_id, skey)
    if summ is None:
        try:
            summ = {"summary": await ai.summarize(text)}
            store.cache_put(doc_id, skey, summ)
        except RateLimitError:
            raise
        except AIError:
            summ = {"summary": ""}

    akey = f"anatomy_{ai.cache_tag('anatomy')}"
    anat = store.cache_get(doc_id, akey)
    if anat is None:
        try:
            anat = await ai.anatomy(text)
            for name in ("problem", "gap", "claim", "method", "limitations"):
                _verify_card_quotes(anat.get(name, {}), text)
            for sub in (anat.get("evidence") or {}).values():
                _verify_card_quotes(sub, text)
            store.cache_put(doc_id, akey, anat)
        except RateLimitError:
            raise
        except AIError:
            anat = {}

    chat_turns = store.chat_get(doc_id)
    glossary = await _get_brief(doc_id, doc, "Korean")

    # Figures + display-math presence (heavy: the layout model runs on every page).
    pdf_bytes = store.pdf_path(doc_id).read_bytes()
    assets = await asyncio.to_thread(pdf_utils.figure_catalog, pdf_bytes)
    figures = assets["figures"]

    rv = await ai.review(
        summary=summ.get("summary", ""),
        anatomy_obj=anat,
        chat_turns=chat_turns,
        figures=[{"id": f["id"], "caption": f["caption"]} for f in figures],
        front_matter=pdf_utils.brief_source(pages),
        glossary=glossary,
    )
    body = rv.get("body", "")

    # Crop only the figures the note links to; drop a link whose crop fails or whose id is unknown
    # so the note never carries a broken image.
    by_id = {f["id"]: f for f in figures}
    crops: dict[str, bytes] = {}
    for fid in _referenced_ids(body):
        f = by_id.get(fid)
        if f is None:
            continue
        try:
            crops[fid] = await asyncio.to_thread(
                pdf_utils.crop_region_png, pdf_bytes, f["page"], f["bbox"]
            )
        except Exception:  # noqa: BLE001 — skip an unreadable crop, keep the note
            pass
    body = _IMG_REF_RE.sub(
        lambda m: m.group(0) if _IMG_ID_RE.match(m.group(0)).group(1) in crops else "",
        body,
    )

    date = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    index_md = (
        _frontmatter(rv, date=date, has_math=assets["has_math"])
        + "\n"
        + body.strip()
        + "\n"
    )
    slug = _slug(rv.get("title") or doc["meta"].get("title", ""), doc_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{slug}/index.md", index_md)
        for fid, png in crops.items():
            z.writestr(f"{slug}/{fid}.png", png)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


class TranslateReq(BaseModel):
    doc_id: str
    page: int  # 0-indexed
    target_lang: str = "Korean"
    segments: list[str] | None = (
        None  # client-supplied segments (from the PDF text layer)
    )
    force: bool = False  # re-translate, ignoring any cached result


@app.post("/api/translate")
async def translate(req: TranslateReq):
    doc = _require_doc(req.doc_id)
    pages = doc["pages"]
    if not (0 <= req.page < len(pages)):
        raise HTTPException(400, "page out of range")

    if req.segments:
        segs = [s for s in req.segments if s.strip()]
    else:
        segs = pdf_utils.segment(pages[req.page].strip())

    # Cache key includes a signature of the exact segments, so a change in how the
    # page is split (different count/content) never returns a mismatched cached result.
    sig = hashlib.sha1("\x1f".join(segs).encode("utf-8")).hexdigest()[:10]
    key = f"tr_{req.target_lang}_{ai.cache_tag('translate')}_{req.page}_{sig}"
    cached = store.cache_get(req.doc_id, key)
    if cached:
        return cached

    if not segs:
        result = {"segments": [], "translation": ""}
        store.cache_put(req.doc_id, key, result)
        return result

    source = "\n\n".join(segs)
    brief = await _get_brief(req.doc_id, doc, req.target_lang)
    tr = await ai.translate_segments(segs, req.target_lang, brief=brief)
    if tr is None:
        # Alignment failed — fall back to one whole-page translation.
        whole = await ai.translate(source, req.target_lang)
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

    # Keep client-supplied segments 1:1 (do NOT drop blank ones): the client numbers results by
    # position within the batch it sent, so filtering here would shift every later index and
    # mis-map the translation and its highlight. Only fall back to splitting the page ourselves
    # when the client sent nothing.
    segs = (
        list(req.segments)
        if req.segments
        else pdf_utils.segment(pages[req.page].strip())
    )
    sig = hashlib.sha1("\x1f".join(segs).encode("utf-8")).hexdigest()[:10]
    key = f"tr_{req.target_lang}_{ai.cache_tag('translate')}_{req.page}_{sig}"

    async def gen():
        if not req.force:
            cached = store.cache_get(req.doc_id, key)
            if cached:
                for k, seg in enumerate(cached["segments"]):
                    yield (
                        json.dumps({"i": k, "tr": seg["tr"]}, ensure_ascii=False) + "\n"
                    )
                return
        if not segs:
            store.cache_put(req.doc_id, key, {"segments": [], "translation": ""})
            return
        # translate_stream yields translations strictly in order and raises AIError if the model
        # drifts (out-of-order / wrong count), so we number them by arrival — never by the model's
        # printed index — and a misaligned stream errors out instead of being shown or cached. On
        # an AIError the client retries the batch and finally falls back to source text.
        collected: list[str] = []
        try:
            brief = await _get_brief(req.doc_id, doc, req.target_lang)
            chunks = ai.translate_stream(segs, req.target_lang, brief=brief)
            try:
                async for tr in chunks:
                    yield (
                        json.dumps({"i": len(collected), "tr": tr}, ensure_ascii=False)
                        + "\n"
                    )
                    collected.append(tr)
            finally:
                await chunks.aclose()
        except AIError as exc:
            limited = isinstance(exc, RateLimitError)
            yield (
                json.dumps(
                    {"error": str(exc), "rate_limited": limited}, ensure_ascii=False
                )
                + "\n"
            )
            return
        # Reaching here means the stream completed in order with exactly one line per segment, so
        # the cache is always complete and aligned.
        pairs = [{"src": segs[k], "tr": collected[k]} for k in range(len(segs))]
        store.cache_put(
            req.doc_id,
            key,
            {"segments": pairs, "translation": "\n\n".join(collected)},
        )

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class ExplainReq(BaseModel):
    doc_id: str
    text: str


@app.post("/api/explain")
async def explain(req: ExplainReq):
    _require_doc(req.doc_id)
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "No text selected")
    answer = await ai.explain(text[:4000])
    return {"explanation": answer}


class ChatReq(BaseModel):
    doc_id: str
    question: str


@app.post("/api/chat")
async def chat(req: ChatReq):
    doc = _require_doc(req.doc_id)
    history = store.chat_get(req.doc_id)
    answer = await ai.chat(
        req.question, paper_text=pdf_utils.full_text(doc["pages"]), history=history
    )
    store.chat_put(
        req.doc_id,
        history
        + [
            {"role": "user", "text": req.question},
            {"role": "assistant", "text": answer},
        ],
    )
    return {"answer": answer}


@app.get("/api/chat/{doc_id}")
async def chat_history(doc_id: str):
    return {"turns": store.chat_get(doc_id)}


@app.post("/api/chat/{doc_id}/reset")
async def chat_reset(doc_id: str):
    store.chat_clear(doc_id)
    return {"ok": True}


def _require_doc(doc_id: str) -> dict:
    doc = store.load_doc(doc_id)
    if doc is None:
        raise HTTPException(404, "Unknown doc_id — upload the PDF again")
    return doc


async def _get_brief(doc_id: str, doc: dict, target_lang: str) -> str:
    """Per-paper translation glossary, generated once (cheap model) from the front matter and
    cached, then injected into every page's translation for cross-page term consistency.
    Best-effort: if generation fails, translation proceeds without it."""
    key = f"brief_{target_lang}_{ai.cache_tag('brief')}"
    cached = store.cache_get(doc_id, key)
    if cached is not None:
        return cached.get("brief", "")
    try:
        src = pdf_utils.brief_source(doc["pages"])
        brief = await ai.translate_brief(src, target_lang)
    except RateLimitError:
        raise
    except Exception:  # noqa: BLE001
        return ""  # non-fatal: a missing glossary just means no extra consistency hint
    store.cache_put(doc_id, key, {"brief": brief})
    return brief


class BriefReq(BaseModel):
    doc_id: str
    target_lang: str = "Korean"


@app.post("/api/brief")
async def brief(req: BriefReq):
    """Warm (or return) the cached translation glossary for a paper."""
    doc = _require_doc(req.doc_id)
    return {"brief": await _get_brief(req.doc_id, doc, req.target_lang)}


async def _backend_rows() -> list[dict]:
    rows = []
    for name, mod in backends.BACKENDS.items():
        provider = None
        if mod.installed():
            try:
                # describe() may start a subprocess (Hermes); keep it off the event loop.
                provider = (await asyncio.to_thread(mod.describe)).get("provider")
            except AIError:
                provider = None
        rows.append(
            {
                "id": name,
                "label": mod.LABEL,
                "installed": mod.installed(),
                "provider": provider,
                "suggestions": mod.MODEL_SUGGESTIONS,
            }
        )
    return rows


async def _first_run_backend(rows: list[dict]) -> tuple[str | None, str | None]:
    installed = [r["id"] for r in rows if r["installed"]]
    if not installed:
        return None, "Install Claude Code or Hermes, sign in, then reload this page."
    if len(installed) > 1:
        return None, "Choose an AI Backend in Settings."
    name = installed[0]
    s = settings.load()
    try:
        await ai.probe(name, settings.model_for(s, name, "translate"))
    except AIError as exc:
        return None, f"{backends.get(name).LABEL} is installed but not ready: {exc}"
    settings.save(name, {}, settings.concurrency_for(s, name))
    return name, None


@app.get("/api/config")
async def config():
    rows = await _backend_rows()
    s = settings.load()
    backend, reason = s["backend"], None
    if backend is None:
        backend, reason = await _first_run_backend(rows)
        s = settings.load()
    return {
        "backend": backend,
        "reason": reason,
        "backends": rows,
        "tasks": list(settings.TASKS),
        "models": s["models"],
        "concurrency": s["concurrency"],
    }


class SettingsReq(BaseModel):
    backend: str
    models: dict[str, str] = {}
    concurrency: int = settings.DEFAULT_CONCURRENCY


@app.post("/api/settings")
async def save_settings(req: SettingsReq):
    if req.backend not in backends.BACKENDS:
        raise HTTPException(400, f"Unknown AI Backend: {req.backend}")
    try:
        settings.validate(req.backend, req.models, req.concurrency)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    merged = {**settings.load()["models"][req.backend], **req.models}
    for model_id in sorted(set(merged.values())):
        try:
            await ai.probe(req.backend, model_id)
        except AIError as exc:
            raise HTTPException(400, f"{model_id}: {exc}") from exc
    try:
        settings.save(req.backend, req.models, req.concurrency)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# --- static UI (mounted last so /api/* wins) ---
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
