# Sundark

A personal, local web app for reading conference/journal papers with AI help:
**summary**, **translation**, a **side-by-side reader**, and **Q&A chat** — with a
**library** that reads PDFs straight from a folder, and **AI settings** to pick the AI tool and models.

It runs on a subscription you already have: AI requests go through the AI Backend you choose,
**Claude Code**, **Hermes**, or **ChatGPT**, using subscription login. There is **no API key and no
per-token bill**.
Inspired by sites like themoonlight.io, but kept simple and private.

> Personal/local use only. No reader access control. Subscription usage limits apply. Only `app/backends/`
> starts AI processes; serving other people would need a billable API backend there.

## Requirements (macOS)

1. **ChatGPT** with eligible plan access, or **Claude Code** / **Hermes** installed and logged in.
   ChatGPT needs no CLI: connect it in AI settings after starting Sundark. Claude Code:
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
   Hermes: install it from https://hermes-agent.nousresearch.com/docs/ and run `hermes setup`
   to pick a Provider and model. Sundark never changes your Hermes settings.
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
cd sundark
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Open <http://localhost:8743>. (On your own Mac, `127.0.0.1` keeps it private to the
machine. Use `--host 0.0.0.0` only if you want other devices on your network to reach it.)

### Layout detection (default) and the install size

Region detection (headings, figures, tables, formulas) uses a layout model,
DocLayout-YOLO, which is **required** — `uv sync` installs it. This pulls `torch` +
`torchvision` + the model, so the install is **~1 GB on macOS/Windows** (CPU) and
larger on Linux (the default torch is the CUDA build). A GPU is used automatically
if present; on CPU it runs ~0.5 s/page and is cached per paper. The model weights
(~100 MB) download from Hugging Face on first use.

The model is loaded at startup and has no heuristic fallback: if it can't load, the
server refuses to start with a clear message instead of serving a degraded parser.
If you install torch by hand, get **torch and torchvision from the same index** (a
mismatched pair fails with `torchvision::nms does not exist`).

Display equations are additionally re-read from the rendered image by a small
vision call (so `\frac`, `\sum`, subscripts come out as correct LaTeX instead of the
scrambled PDF text), cached per paper.

### Run without uv (plain python3 + pip)

```bash
cd sundark
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8743
```
(If `python3` is missing: `brew install python`, or install Xcode Command Line Tools.)
Next time you only need `source .venv/bin/activate` before the `uvicorn` line.

## Papers folder (the library)

Put your PDFs in **`sundark/papers/`** and they show up in the left sidebar.
Click one to read it; hit ⟳ to rescan after adding files.

Use a different folder without moving files:
```bash
PAPERS_DIR="$HOME/Documents/papers" uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```
You can still **Upload PDF** from the top bar for one-off files.

## Use

1. Pick a paper from the **Library**, or **Upload PDF**.
2. **AI** badge (top right): choose the AI Backend and a model per task (see
   *AI settings*). Saving reloads the page; results are cached per backend and model.
3. **Summary** tab → *Generate summary*.
4. **Translation** tab → pick a language (default Korean); it translates the page
   shown on the left and follows you as you page through.
5. **Chat** tab → ask questions about the paper.

Results are cached per paper **and per AI Backend and model** under `data/`, so reopening is instant
and doesn't re-spend usage. Text-based PDFs only (scanned images need OCR — not yet).

Over the PDF pane, **Ctrl + mouse wheel** zooms just the paper around the pointer.
Over the library, AI panel, or top bar, it keeps the browser's whole-app zoom.
The PDF's **+ / −**, **fit width**, and **fit page** controls remain available.

### Paper identity and titles

When you open a PDF, Sundark looks for its DOI or arXiv ID on the first two pages and
fetches the title, authors, and year from CrossRef or arXiv. Only that identifier is sent. If
there is no identifier or no internet, the title read from the PDF is used, and the next online
open fills it in. Chat history follows the paper, not the file: an annotated copy or a newer
arXiv version of the same paper continues the same chat. Translations are still cached per
exact file.

## AI settings

Click the **AI** badge in the top bar to choose the AI Backend (Claude Code, Hermes, or ChatGPT), a model
for each task, and how many translation requests run at once (default 8). Saving sends one short
test request per model and refuses models your account cannot use. Settings are stored in
`data/settings.json`. `SUNDARK_BACKEND=claude|hermes|chatgpt` overrides the saved choice for one run (the name from
before the rename, `PAPER_READER_BACKEND`, still works).

On first launch, if only one of the tools is installed, Sundark picks it after a successful
test request. If both are installed, the settings dialog opens. If neither is, PDFs still open and
AI features stay off.

## Layout

```
app/
  main.py        FastAPI routes + static serving + library/open/pdf endpoints
  ai.py          every prompt; picks the model for each task from settings
  settings.py    AI Backend, model per task, concurrency (data/settings.json)
  backends/      one module per AI Backend (claude.py, hermes.py, chatgpt.py); only place AI runs
  pdf_utils.py   PyMuPDF text extraction
  store.py       local PDF + result cache
web/             static reader UI (pdf.js + marked, no build step)
papers/          your PDFs (gitignored)
data/            runtime cache (gitignored)
```

## Config

- AI Backend and models: the **AI** badge, stored in `data/settings.json`; defaults in
  `DEFAULT_MODELS` in `app/settings.py`.
- Default translation language: the `<select>` in `web/index.html`.
- Papers folder: `PAPERS_DIR` env var (default `./papers`).

### Continue with ChatGPT

Open Sundark at `http://127.0.0.1:8743`, choose **ChatGPT** in AI settings,
and click **Continue with ChatGPT**. The browser registers Sundark with OpenAI
and asks permission to use your eligible ChatGPT plan. Return to Sundark, refresh
account status, choose the account's available models, and save settings. Saving
checks each selected model with a small real request that uses your plan.

Credentials remain on the local server in `~/.config/sundark/chatgpt.json`,
written atomically with owner-only permissions on POSIX. On Windows, files
inherit the chosen directory's ACL; keep that directory restricted to your
Windows account. `SUNDARK_CHATGPT_CREDENTIALS`
changes that location. Do not commit or share this file. Sundark keeps its own
registration and does not read Codex credentials or require an API key.
You can add accounts, select a saved account, and sign out in AI settings.
If remote revocation fails, local credentials are still removed; remove the
Sundark registration in ChatGPT's connected-app settings as well.

This backend uses the official public Responses API with ChatGPT OAuth, inline
images, no agent tools, `store:false`, and streamed responses. Account sign-in
and a model list alone do not prove plan access; a completed AI request does.
See [OpenAI's sign-in guide](https://developers.openai.com/siwc/token-sharing-open-source/sign-in).
