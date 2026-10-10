# Install

paper-reader is a local web app: whole-document PDF translation, a grounded "Research Anatomy",
an auto summary, and paper-aware chat. It runs on a subscription you already have, through
**Claude Code** or **Hermes** — no API key.

## Requirements

- **Claude Code or Hermes, installed and logged in.** Claude Code (with no API key set):
  ```bash
  claude -p "hi"        # should answer; ANTHROPIC_API_KEY must be unset
  ```
  Hermes: run `hermes setup` once to choose a Provider and model.
- **uv** (package manager) and **Python 3.12** (pinned in `.python-version`).
- ~1–2 GB free space — the install pulls `torch` + `torchvision` + the layout model.

## Install

```bash
uv sync
```

Installs everything, including the **required** layout model (DocLayout-YOLO) and spaCy. On first
run two models download automatically: the YOLO weights (~100 MB, from Hugging Face) and the spaCy
`en_core_web_sm` sentence model (~12 MB).

## Run

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Open <http://localhost:8743>. `127.0.0.1` keeps it private to the machine; use `--host 0.0.0.0`
only to reach it from other devices on your network.

The layout model loads at startup and **is required** — if it can't load, the server refuses to
start with a clear message (there is no heuristic fallback).

## Install without uv (plain python3 + pip)

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
python -m uvicorn app.main:app --host 127.0.0.1 --port 8743
```

## Troubleshooting

- **`torchvision::nms does not exist` at startup** — torch and torchvision came from different
  indexes. Reinstall both from the **same** index, e.g. the CPU wheels:
  ```bash
  uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  ```
- **Server won't start / "layout model failed to load"** — the model couldn't load or download;
  fix the torch install (above) and that the weights can download, then restart.
- **macOS: install fails mid-way / missing files** — don't install inside an iCloud-synced folder
  (e.g. under Desktop); sync evicts files during install. Use a plain folder like `~/paper-reader`.
- **Python 3.14** is too new (pymupdf and torch have no wheels) — use 3.12.
- **First layout pass is slow** — YOLO runs per page on CPU (~0.5 s/page) and is cached per paper;
  a GPU is used automatically if present.
- **Stale results after an update** — `rm -rf data` for a clean rebuild.
- **AI badge says *not set*** — open it and pick an AI Backend; the dialog says what is missing.
- **Hermes models** — ChatGPT-account Codex supports fewer models than the Hermes catalog lists;
  the test request on Save tells you which ones fail.

See `README.md` for usage and `AGENTS.md` for the architecture and conventions.
