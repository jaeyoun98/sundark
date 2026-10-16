"""Real app with isolated storage and deterministic external service boundaries."""

import asyncio
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf


def deny_external(self, request, **kwargs):
    raise AssertionError(f"Unexpected external HTTP: {request.url.host}")


# Metadata/auth lookups must never leave this isolated fixture.
httpx.Client.send = deny_external
httpx.AsyncClient.send = deny_external

ROOT = tempfile.TemporaryDirectory(prefix="sundark-e2e-")
root = Path(ROOT.name)
os.environ["SUNDARK_SETTINGS"] = str(root / "settings.json")
os.environ["SUNDARK_CHATGPT_CREDENTIALS"] = str(root / "chatgpt.json")
os.environ["PAPERS_DIR"] = str(root / "library")
for key in ("SUNDARK_BACKEND", "PAPER_READER_BACKEND", "PAPER_READER_SETTINGS"):
    os.environ.pop(key, None)

from app import backends, layout_model, pdf_utils, settings, store  # noqa: E402

for name in ("DATA", "PDFS", "DOCS", "CACHE", "CHATS", "PAPERS"):
    directory = root / name.lower()
    directory.mkdir()
    setattr(store, name, directory)

TEXT = {"Korean": "연구 결과를 설명한다", "Japanese": "研究の結果を説明する"}
CALLS = Counter()


def language(prompt, system):
    match = re.search(r"Write all generated prose and headings in (\w+)", system or "")
    if not match:
        match = re.search(r"(?:segment|text inside <paper>) into (\w+)", prompt)
    if not match:
        raise AssertionError("AI request did not specify its output language")
    requested = re.findall(
        r"Write all generated prose and headings in (\w+)", system or ""
    )
    if len(set(requested)) > 1:
        raise AssertionError("Conflicting output languages")
    return match[1]


async def run(prompt, *, model, system=None, images=()):
    if prompt == "Reply with: ok":
        return "ok"
    lang = language(prompt, system)
    prose = TEXT[lang]
    if "Research Anatomy" in prompt:
        task = "anatomy"
    elif "SINGLE JSON object" in prompt:
        task = "review"
    elif "translation brief" in prompt:
        task = "brief"
    elif "Summarize the academic paper" in prompt:
        task = "summary"
    elif "Answer my questions using only the paper" in prompt:
        task = "chat"
    else:
        raise AssertionError("Unexpected AI request")
    CALLS[f"{task}:{lang}"] += 1
    if "Research Anatomy" in prompt:
        card = {
            "status": "present",
            "points": [
                {
                    "text": prose,
                    "kind": "author_stated",
                    "quote": "We compare two methods.",
                }
            ],
        }
        return json.dumps(
            {key: card for key in ("problem", "gap", "claim", "method", "limitations")}
        )
    if "SINGLE JSON object" in prompt:
        return json.dumps(
            {
                "title": "Browser Study",
                "paper_title": "Browser Study",
                "authors": [],
                "venue": "",
                "year": None,
                "tags": [],
                "summary": prose,
                "body": "## Review\n\n" + prose,
            }
        )
    if "translation brief" in prompt:
        return "FIELD: Research\nGLOSSARY: study -> study"
    await asyncio.sleep(0.03)
    return prose


async def stream(prompt, *, model, system=None):
    lang = language(prompt, system)
    CALLS[f"translate:{lang}"] += 1
    source = prompt.split("<segments>\n", 1)[1].split("</segments>", 1)[0]
    for index in re.findall(r"^(\d+)\. ", source, re.M):
        await asyncio.sleep(0.03)
        yield f"{index}\t{TEXT[lang]}\n"


backends.BACKENDS = {
    "claude": SimpleNamespace(
        LABEL="Deterministic test backend",
        MODEL_SUGGESTIONS=["sonnet"],
        installed=lambda: True,
        describe=lambda: {"provider": "E2E fixture"},
        run=run,
        stream=stream,
    )
}
# Exercise the supported offline sentence splitter without downloading a model.
pdf_utils._SPACY_FAILED = True
layout_model.available = lambda: True
layout_model.detect_page = lambda page: [
    {
        "role": "body",
        "cls": "plain text",
        "x0": 0,
        "y0": 0,
        "x1": page.rect.width,
        "y1": page.rect.height,
        "conf": 1,
    }
]
settings.save("claude", {}, 2)

from app.main import app  # noqa: E402, F401

pdf = pymupdf.open()
page = pdf.new_page()
page.insert_text((72, 72), "Browser Study\nAbstract")
for y in (115, 200, 285):
    page.insert_text(
        (72, y),
        "We compare two methods. The study measures reliable translation.\nOur experiment provides reproducible evidence for this comparison.",
    )
pdf.set_metadata({"title": "Browser Study"})
pdf.save(root / "sample.pdf")
pdf.close()


@app.get("/__e2e/sample.pdf")
async def sample():
    from fastapi.responses import FileResponse

    return FileResponse(root / "sample.pdf", media_type="application/pdf")


# The production static mount is deliberately last; test-only routes precede it.
app.router.routes.insert(-2, app.router.routes.pop())


@app.get("/__e2e/calls")
async def calls():
    return dict(CALLS)


app.router.routes.insert(-2, app.router.routes.pop())
