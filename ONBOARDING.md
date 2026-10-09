# Onboarding — paper-reader

A personal, local web app that reads conference/journal PDFs and gives whole-document
translation, a grounded "Research Anatomy", an auto summary, and paper-aware Q&A chat. It runs on
your **Claude Code subscription via the logged-in `claude` CLI** — no API key.

This guide gets an agent (or a new developer) from a clean checkout to a running app, and points
at the rules to follow. Read `AGENTS.md` before changing code; see `KNOWN_ISSUES.md` for open bugs.

## 1. Prerequisites

- **`claude` CLI, logged in with a subscription.** Verify (with no API key set):
  ```bash
  claude -p "hi"        # should answer; run it with ANTHROPIC_API_KEY unset
  ```
  The app shells out to this CLI and strips `ANTHROPIC_API_KEY` from the subprocess env.
- **uv** (package manager) and **Python 3.12** (pinned in `.python-version`; 3.14 breaks pymupdf
  and torch, 3.11 also works but 3.12 is the pin).
- ~1–2 GB free space: `uv sync` pulls `torch` + `torchvision` + DocLayout-YOLO.

## 2. Install

```bash
uv sync
```

This installs everything, including the **required** layout model (DocLayout-YOLO) and spaCy. On
first run two models download automatically: the YOLO weights (~100 MB, from Hugging Face) and the
spaCy `en_core_web_sm` sentence model (~12 MB).

Gotcha: if you ever install torch by hand, get **torch and torchvision from the same index** — a
mismatched pair fails at import with `torchvision::nms does not exist`. On macOS install **outside**
an iCloud-synced folder (e.g. not under Desktop), or sync evicts files mid-install.

## 3. Run

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Open <http://localhost:8743>. The layout model loads at startup; **it is required** — if it can't
load, the server refuses to start with a clear message (there is no heuristic fallback). `127.0.0.1`
keeps it private to the machine.

## 4. Use

- **Upload PDF** (top bar) or pick one from the **Library** (it scans a papers folder).
- On open you get a brief loading cover, then: the **Translation** panel streams top-to-bottom
  (hover a sentence to highlight its source in the PDF; select PDF text to ask about it in Chat),
  plus **Anatomy**, **Summary**, and **Chat** tabs.
- Model picker defaults to **Haiku 5.5** (fast, cheap, renders inline math as LaTeX). Switch to
  Sonnet/Opus for the heaviest reasoning. Language picker changes the translation target.
- Results cache per paper/model under `data/`, so reopening is instant.

## 5. Conventions (for agents changing the code)

- Python: **uv** + **ruff** (`line-length = 88`). Before committing: `uvx ruff format .` and keep
  `uvx ruff check .` clean. CI runs both.
- **Never hardcode an API key/token/password** anywhere (tests included) — this app needs none.
- Do **not** commit `data/` or `papers/` (gitignored: uploaded PDFs + cache).
- PDF text is **untrusted input**: it is framed as data in prompts and the CLI runs with tools
  disabled (`--allowed-tools ""`), text passed on stdin. Keep it that way.
- All Claude calls live in **`app/claude.py` only**. Region typing is YOLO-only in
  `app/layout_model.py` + `app/pdf_utils.py`. See `AGENTS.md` for the full architecture and the
  many behaviour notes (headers/figures/tables dropped, cross-page stitching, caching, etc.).

## 6. Troubleshooting

- **Server won't start / "layout model failed to load"**: torch/torchvision mismatch or the model
  couldn't download — fix the install (see §2) and restart.
- **Stale results after a parser change**: `rm -rf data` for a clean rebuild (the layout cache is
  version-tagged, so most changes are bypassed automatically, but a full wipe is the sure reset).
- **Occasional untranslated (English) sentence**: the model rarely drops a line under heavy
  parallelism; it's retried and otherwise shown as source text. Lower `TR_POOL` in `web/app.js`
  (default 8) if your subscription rate-limits concurrent calls.
- **First layout pass is slow**: YOLO runs per page on CPU (~0.5 s/page) and is cached; a GPU is
  used automatically if present.
