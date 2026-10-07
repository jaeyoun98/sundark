# Paper Reader

A personal, local web app for reading conference/journal papers with AI help:
**summary**, **translation**, a **side-by-side reader**, and **Q&A chat** — with a
**library** that reads PDFs straight from a folder, and a **model picker**.

It runs on your **Claude Code subscription** — the backend shells out to the
logged-in `claude` CLI, so there is **no Anthropic API key and no per-token bill**.
Inspired by sites like themoonlight.io, but kept simple and private.

> Personal/local use only. No auth. Subscription usage limits apply. To serve other
> people you must switch `app/claude.py` to the billable Claude API — that one module
> is the only seam that talks to Claude.

## Requirements (macOS)

1. **Claude Code CLI**, logged in with your subscription:
   ```bash
   # if you don't have it yet:
   curl -fsSL https://claude.ai/install.sh | bash      # or: npm i -g @anthropic-ai/claude-code
   claude            # sign in once (opens browser); pick your Pro/Max account
   ```
   Verify it answers without an API key:
   ```bash
   unset ANTHROPIC_API_KEY
   claude -p "say hi"
   ```
2. **Python 3.10+**. Either use **uv** (recommended) or plain `python3` + pip:
   ```bash
   # Option A — uv (installs Python for you if needed)
   brew install uv                               # or: curl -LsSf https://astral.sh/uv/install.sh | sh
   # Option B — no uv: use the python3 already on your Mac (see "Run without uv" below)
   ```

> If `ANTHROPIC_API_KEY` is set in your shell, the app would bill that key. This app
> strips it per call to force subscription auth, but it's cleanest to leave it unset.

## Run

```bash
cd paper-reader
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Open <http://localhost:8743>. (On your own Mac, `127.0.0.1` keeps it private to the
machine. Use `--host 0.0.0.0` only if you want other devices on your network to reach it.)

### Run without uv (plain python3 + pip)

```bash
cd paper-reader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8743
```
(If `python3` is missing: `brew install python`, or install Xcode Command Line Tools.)
Next time you only need `source .venv/bin/activate` before the `uvicorn` line.

## Papers folder (the library)

Put your PDFs in **`paper-reader/papers/`** and they show up in the left sidebar.
Click one to read it; hit ⟳ to rescan after adding files.

Use a different folder without moving files:
```bash
PAPERS_DIR="$HOME/Documents/papers" uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```
You can still **Upload PDF** from the top bar for one-off files.

## Use

1. Pick a paper from the **Library**, or **Upload PDF**.
2. **Model** (top right): Sonnet (balanced, default) · Opus (best) · Haiku (fastest).
   Switching re-generates results under the new model.
3. **Summary** tab → *Generate summary*.
4. **Translation** tab → pick a language (default Korean); it translates the page
   shown on the left and follows you as you page through.
5. **Chat** tab → ask questions about the paper.

Results are cached per paper **and per model** under `data/`, so reopening is instant
and doesn't re-spend usage. Text-based PDFs only (scanned images need OCR — not yet).

## Layout

```
app/
  main.py        FastAPI routes + static serving + library/open/pdf endpoints
  claude.py      the only module that calls Claude (subscription CLI); model control
  pdf_utils.py   PyMuPDF text extraction
  store.py       local PDF + result cache
web/             static reader UI (pdf.js + marked, no build step)
papers/          your PDFs (gitignored)
data/            runtime cache (gitignored)
```

## Config

- Default model: `DEFAULT_MODEL` in `app/claude.py`.
- Default translation language: the `<select>` in `web/index.html`.
- Papers folder: `PAPERS_DIR` env var (default `./papers`).
