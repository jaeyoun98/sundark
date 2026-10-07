# AGENTS.md — rules for anyone (human or AI) changing this repo

Read this before editing. It captures the non-obvious decisions that keep this
project working. Keep changes surgical and in the existing style.

## What this is

A personal, local web app that reads conference/journal PDFs and provides
whole-document translation, a grounded "Research Anatomy", an auto summary, and
paper-aware Q&A chat. It runs the user's **Claude Code subscription** — not a
billable API key.

## Architecture and boundaries

- **`app/claude.py` is the only module that talks to Claude.** It shells out to
  the logged-in `claude` CLI (subscription auth). It deliberately strips
  `ANTHROPIC_API_KEY` from the subprocess env. To make this a public/multi-user
  service, replace **only this file** with the billable Claude API; do not spread
  Claude calls into other modules.
- **`app/pdf_utils.py`** turns a PDF page into word geometry + sentences:
  `get_text("dict")` for per-word position/size/bold, figure-region and margin
  filtering, font/style heading detection, paragraph reconstruction, and sentence
  splitting via **pysbd** (with a regex fallback). Keep it **format-agnostic** —
  it must work across many paper layouts, so do not add rules that fit one paper.
  Validate changes against several papers, not one.
- **`app/main.py`** is FastAPI: upload/library, `/api/layout`, streaming
  translation, summarize, anatomy, chat. **`web/`** is plain JS + pdf.js + KaTeX,
  no build step. Highlighting maps server word coordinates (PDF points) to the
  viewport, so highlights are sentence-accurate.
- **`app/store.py`** persists uploads + cached AI results under `data/`.

## Security (do not weaken)

- Never hardcode an API key, token, or password anywhere — including tests and
  comments. This app needs no API key; it uses the subscription CLI.
- PDF text is **untrusted input**. It is framed as data in prompts and the CLI is
  run with tools disabled (`--allowed-tools ""`). Keep both. Pass text on stdin,
  never interpolated into a shell command.

## Conventions

- Python: **uv** + **ruff**, `line-length = 88` (team standard). CI runs
  `ruff format --check .` and `ruff check .`. Run `uvx ruff format .` before you
  commit; keep `uvx ruff check .` clean.
- Do **not** commit `data/` or `papers/` (gitignored: uploaded PDFs + cache).
- Commit messages: imperative, subject under 72 chars, no emoji. Co-author
  trailer: `Co-authored-by: Claude Code <noreply@anthropic.com>`.

## Run it

```bash
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Requires the `claude` CLI logged in with a subscription (`claude`, then sign in).
Verify once: `claude -p "hi"` answers with `ANTHROPIC_API_KEY` unset.

## Behavior notes (so you don't "fix" them by mistake)

- **Translation is whole-document and sequential.** The `claude` CLI is not safe
  to invoke concurrently, so pages translate one at a time, top to bottom.
- **Default model is Sonnet** — it renders inline math as LaTeX (KaTeX). Haiku is
  faster but leaves inline math as plain text. Model is switchable in the UI.
- Results are cached per paper/model under `data/`, so reopening is instant.
- Known limits: scanned/image-only PDFs are unsupported (no OCR); labels inside a
  teaser diagram on a first page can leak; the compiled PDF has no LaTeX source,
  so math is reconstructed by the model, not parsed.
