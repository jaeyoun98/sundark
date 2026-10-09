# AI Backend Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Paper Reader send every AI Task through either Claude Code or Hermes, chosen in an in-app settings dialog, with a Model per Task, on top of main's parallel paragraph-batch translation.

**Architecture:** `app/backends/` holds one module per AI Backend behind a tiny shared contract (`run`, `stream`, `installed`, `describe`) plus a registry so OpenClaw can be added later as one more module. `app/ai.py` owns every prompt (moved from main's `app/claude.py`), resolves the Model for each Task from `app/settings.py`, and bounds concurrency per AI Backend. `app/main.py` keeps its routes but calls `ai`, keys caches by `ai.cache_tag(task)`, persists Chat History as an app-owned transcript, and maps rate limits to HTTP 429. The UI drops the global model dropdown for a settings dialog and stops translating on a rate limit.

**Tech Stack:** Python 3.12, FastAPI, asyncio subprocesses, Claude Code CLI (`claude -p` with stream-json input/output), Hermes SDK run in Hermes's own Python through a JSON-lines runner, vanilla JS, `unittest` (`uv run python -m unittest discover -s tests`), Node for JS checks.

**Spec:** The design decisions Q1–Q21 agreed in the grilling session, recorded in `GLOSSARY.md`, `docs/adr/0001-paper-id-separate-from-file-fingerprint.md`, and `docs/adr/0002-subscription-only-and-no-tools-on-paper-input.md`. This plan covers the AI Backend half. Paper ID, Bibliographic Record lookup, and re-keying Chat History by Paper ID are the next plan; this plan keys Chat History by the existing `doc_id`.

## Global Constraints

- Supported AI Backends: `claude` (Claude Code) and `hermes` (Hermes). OpenClaw is documented as a future backend only; it never appears in the UI.
- Subscription access only: the Claude Code subprocess env must not contain `ANTHROPIC_API_KEY`; Paper Reader never changes the Hermes Provider.
- Every request carrying PDF text or a PDF image runs with all agent tools disabled. Claude: `--tools ""`. Hermes: `enabled_toolsets=[]` and a refusal if the agent reports any tool. Equation images go inline in the message, never through a file-read tool.
- One AI Backend for the whole app; a Model per Task. Tasks: `translate`, `summary`, `anatomy`, `chat`, `explain`, `equation`, `brief`.
- Default Models — Claude Code: `translate` `claude-haiku-5-5`, `brief` `claude-haiku-5-5`, `equation` `claude-haiku-5-5`, all others `sonnet`. Hermes: `translate` `gpt-6-luna`, `brief` `gpt-6-luna`, `equation` `gpt-6-luna`, all others `gpt-6.1-sol`.
- Concurrency is a per-backend setting, default 8, allowed 1–16.
- Settings live in `data/settings.json` (already git-ignored). `PAPER_READER_BACKEND` env overrides the saved backend (dev use).
- Saving settings sends one probe request per distinct Model; any failure rejects the save with the reason.
- Caches stay keyed per AI Backend + Model.
- Chat History is an app-owned transcript sent with every chat request on either backend; Claude `--resume` is no longer used.
- A rate limit stops the remaining translation batches, leaves untranslated sentences as source text, and shows a banner.
- Tests use `unittest` and must not call a real AI Backend. `fastapi.testclient` needs `httpx`, which is already in `uv.lock` as a transitive dependency.
- Before every commit run `uvx ruff format . && uvx ruff check --fix .`; CI runs `ruff format --check` and import sorting.

## Review Focus

- **Neither CLI installed, or not logged in:** PDFs still open and render; AI controls are disabled and the settings dialog explains what to install. Test in Task 6 (`test_config_with_no_backend_installed`).
- **`data/settings.json` is corrupt or hand-edited with an unknown backend/Task:** the app falls back to defaults instead of crashing at startup. Test in Task 2 (`test_corrupt_settings_fall_back_to_defaults`).
- **Rate limit mid-translation:** remaining batches stop, nothing hangs, untranslated sentences show source text, a banner explains it. Tests in Task 5 (`test_rate_limit_in_stream_is_reported_not_retried`) and Task 7 (frontend check).
- **Model saved that the account cannot use (e.g. `gpt-6.1-luna` on ChatGPT Codex):** the save is rejected with the provider's reason and the old settings stay. Test in Task 6 (`test_settings_save_rejects_unavailable_model`).
- **Switching AI Backend with a paper open:** caches do not mix between backends, and the paper's Chat History continues on the new backend. Tests in Task 6 (`test_cache_keys_differ_per_backend`, `test_chat_history_survives_backend_switch`).

---

## File Structure

| File | Responsibility |
|---|---|
| `app/errors.py` (create) | `AIError`, `RateLimitError`, `ModelUnavailableError`, and `classify(text)` |
| `app/backends/__init__.py` (create) | Registry `BACKENDS` and `get(name)`; the one place a new AI Backend is registered |
| `app/backends/process.py` (create) | Run a CLI with stdin input, timeout, and guaranteed child cleanup; streaming variant |
| `app/backends/claude.py` (create) | Claude Code transport: stream-json in/out, inline images, tools off |
| `app/backends/hermes.py` (create) | Hermes transport: locate Hermes Python, run the runner, parse JSON lines |
| `app/backends/hermes_runner.py` (create) | Runs inside Hermes's Python; builds a tool-less `AIAgent`; emits delta/result/error lines |
| `app/settings.py` (create) | Load/save `data/settings.json`, defaults, Model per Task, concurrency, first-run choice |
| `app/ai.py` (create, replaces `app/claude.py`) | All prompts; Task→Model resolution; per-backend concurrency; `cache_tag` |
| `app/claude.py` (delete) | Content moves to `app/ai.py` and `app/backends/claude.py` |
| `app/store.py` (modify) | Add `chat_get`, `chat_put`, `chat_clear` |
| `app/main.py` (modify) | Call `ai`; cache keys; 429 mapping; chat transcript; `/api/config`, `/api/settings` |
| `web/index.html`, `web/app.js`, `web/styles.css` (modify) | Settings dialog, backend badge, concurrency from config, rate-limit stop, chat history |
| `tests/` (create) | `unittest` suites per task, plus `tests/frontend_ratelimit.test.js` |
| `.github/workflows/format-check.yml` (modify) | Run unit tests and the JS check in CI |
| `README.md`, `AGENTS.md`, `INSTALL.md`, `GLOSSARY.md` (modify) | Document backends, settings, adding an AI Backend |

---

### Task 1: Errors, process runner, and test harness

**Files:**
- Create: `app/errors.py`, `app/backends/__init__.py` (empty registry for now), `app/backends/process.py`
- Create: `tests/__init__.py`, `tests/test_process.py`, `tests/test_errors.py`
- Modify: `.github/workflows/format-check.yml`

**Interfaces:**
- Produces: `app.errors.AIError(RuntimeError)`, `RateLimitError(AIError)`, `ModelUnavailableError(AIError)`, `classify(text: str) -> type[AIError]`.
- Produces: `app.backends.process.run_process(args: list[str], stdin: bytes, env: dict, timeout: float = 300) -> bytes` and `stream_process(args, stdin, env, timeout=300) -> AsyncIterator[str]` (yields decoded stdout lines). Non-zero exit raises `AIError` built with `classify(stderr)`; stderr text is never put in the exception message.

- [ ] **Step 1: Write the failing tests**

`tests/__init__.py` is empty. `tests/test_errors.py`:

```python
import unittest

from app.errors import AIError, ModelUnavailableError, RateLimitError, classify


class ClassifyTests(unittest.TestCase):
    def test_rate_limit_phrases(self):
        for text in (
            "HTTP 429 Too Many Requests",
            "Claude AI usage limit reached",
            "rate_limit_error",
            "You exceeded your current quota",
        ):
            self.assertIs(classify(text), RateLimitError, text)

    def test_unavailable_model_phrases(self):
        for text in (
            "The 'gpt-6.1-luna' model is not supported when using Codex with a ChatGPT account.",
            "model_not_found",
            "Invalid model: foo",
        ):
            self.assertIs(classify(text), ModelUnavailableError, text)

    def test_other_text_is_plain_error(self):
        self.assertIs(classify("connection reset"), AIError)
        self.assertTrue(issubclass(RateLimitError, AIError))
```

`tests/test_process.py`:

```python
import asyncio
import sys
import unittest

from app.backends.process import run_process, stream_process
from app.errors import AIError, RateLimitError


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_passes_stdin(self):
        out = await run_process(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
            b"paper",
            {},
        )
        self.assertEqual(out.strip(), b"PAPER")

    async def test_nonzero_exit_hides_stderr_but_classifies(self):
        args = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('429 secret'); sys.exit(3)",
        ]
        with self.assertRaises(RateLimitError) as ctx:
            await run_process(args, b"", {})
        self.assertNotIn("secret", str(ctx.exception))

    async def test_timeout_kills_child(self):
        args = [sys.executable, "-c", "import time; time.sleep(30)"]
        with self.assertRaises(AIError):
            await run_process(args, b"", {}, timeout=0.5)

    async def test_stream_yields_lines(self):
        args = [sys.executable, "-c", "print('a'); print('b')"]
        lines = [line.strip() async for line in stream_process(args, b"", {})]
        self.assertEqual(lines, ["a", "b"])

    async def test_closing_stream_early_reaps_child(self):
        args = [
            sys.executable,
            "-c",
            "import time\nfor i in range(100):\n print(i, flush=True); time.sleep(0.05)",
        ]
        gen = stream_process(args, b"", {})
        await gen.__anext__()
        await gen.aclose()  # must not hang or leak the child
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_errors tests.test_process -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.errors'`.

- [ ] **Step 3: Write the implementation**

`app/errors.py`:

```python
"""Errors shared by every AI Backend. Messages are safe to show in the UI: they never
contain prompt text, PDF content, credentials, or raw provider error bodies."""

from __future__ import annotations

import re


class AIError(RuntimeError):
    """An AI Backend could not complete a request."""


class RateLimitError(AIError):
    """The user's subscription usage limit (or a provider rate limit) was hit."""


class ModelUnavailableError(AIError):
    """The chosen Model cannot be used with this account or AI Backend."""


_RATE = re.compile(r"\b429\b|rate.?limit|usage limit|too many requests|quota", re.I)
_MODEL = re.compile(
    r"model[^.]{0,80}not supported|model_not_found|invalid model|unknown model|"
    r"model[^.]{0,40}does not exist|not_found_error",
    re.I,
)


def classify(text: str) -> type[AIError]:
    """Map raw backend/provider error text to an error class (the text itself is dropped)."""
    if _RATE.search(text or ""):
        return RateLimitError
    if _MODEL.search(text or ""):
        return ModelUnavailableError
    return AIError


def from_text(text: str, fallback: str) -> AIError:
    """Build a safe error: the class comes from the raw text, the message does not."""
    cls = classify(text)
    if cls is RateLimitError:
        return cls("Subscription usage limit reached; try again later")
    if cls is ModelUnavailableError:
        return cls("This model is not available for your account on this AI Backend")
    return cls(fallback)
```

`app/backends/__init__.py`:

```python
"""AI Backends. Each module exposes NAME, LABEL, installed(), describe(), run(), stream().
Register a new AI Backend (e.g. OpenClaw) by importing it here and adding it to BACKENDS."""

from __future__ import annotations

BACKENDS: dict = {}


def get(name: str):
    return BACKENDS[name]
```

`app/backends/process.py`:

```python
"""Bounded CLI execution with stdin input and reliable child cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..errors import AIError, from_text

ROOT = Path(__file__).resolve().parents[2]


async def _stop(proc) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.wait()


async def _spawn(args, env, limit=2**22):
    try:
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or None,
            cwd=ROOT,
            limit=limit,
        )
    except OSError as exc:
        raise AIError(
            f"Cannot start {args[0]}; install the selected AI Backend"
        ) from exc


async def run_process(
    args: list[str], stdin: bytes, env: dict, timeout: float = 300
) -> bytes:
    proc = await _spawn(args, env)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=stdin), timeout)
    except asyncio.TimeoutError as exc:
        await _stop(proc)
        raise AIError("AI request timed out") from exc
    except BaseException:
        await _stop(proc)
        raise
    if proc.returncode != 0:
        raise from_text(
            stderr.decode("utf-8", "replace")
            + stdout.decode("utf-8", "replace")[-2000:],
            f"AI Backend exited {proc.returncode}; check its login and model",
        )
    return stdout


async def stream_process(
    args: list[str], stdin: bytes, env: dict, timeout: float = 300
):
    proc = await _spawn(args, env)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def feed():
        proc.stdin.write(stdin)
        await proc.stdin.drain()
        proc.stdin.close()

    writer = asyncio.create_task(feed())
    errors = asyncio.create_task(proc.stderr.read())
    try:
        while True:
            raw = await asyncio.wait_for(
                proc.stdout.readline(), max(0, deadline - loop.time())
            )
            if not raw:
                break
            yield raw.decode("utf-8", "replace")
        await asyncio.wait_for(proc.wait(), max(0, deadline - loop.time()))
        if proc.returncode != 0:
            err = await errors
            raise from_text(
                err.decode("utf-8", "replace"),
                f"AI Backend exited {proc.returncode}; check its login and model",
            )
    except asyncio.TimeoutError as exc:
        raise AIError("AI stream timed out") from exc
    finally:
        for task in (writer, errors):
            if not task.done():
                task.cancel()
        await asyncio.gather(writer, errors, return_exceptions=True)
        await _stop(proc)
```

Add to `.github/workflows/format-check.yml` after the "Check formatting" step:

```yaml
      - name: Install project dependencies
        run: |
          pip install uv
          uv sync --frozen

      - name: Unit tests
        run: uv run python -m unittest discover -s tests -v

      - name: Check browser JavaScript
        run: node --check web/app.js
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_errors tests.test_process -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/errors.py app/backends/__init__.py app/backends/process.py tests/__init__.py tests/test_errors.py tests/test_process.py .github/workflows/format-check.yml
git commit -m "Add shared AI errors, CLI process runner, and unit test harness"
```

---

### Task 2: Settings store

**Files:**
- Create: `app/settings.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `app.store.DATA` (exists on main).
- Produces:
  - `TASKS: tuple[str, ...] = ("translate", "summary", "anatomy", "chat", "explain", "equation", "brief")`
  - `DEFAULT_MODELS: dict[str, dict[str, str]]` keyed by backend name then Task.
  - `DEFAULT_CONCURRENCY = 8`
  - `settings_path() -> Path` (env `PAPER_READER_SETTINGS` overrides, default `data/settings.json`)
  - `load() -> dict` → `{"backend": str | None, "models": {backend: {task: model}}, "concurrency": {backend: int}}`, always complete and valid.
  - `save(backend: str, models: dict[str, str], concurrency: int) -> None` (only the given backend's entries change).
  - `model_for(settings: dict, backend: str, task: str) -> str`
  - `concurrency_for(settings: dict, backend: str) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:

```python
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"
        self.env = patch.dict(
            os.environ, {"PAPER_READER_SETTINGS": str(self.path)}, clear=False
        )
        self.env.start()
        os.environ.pop("PAPER_READER_BACKEND", None)

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_defaults_when_file_missing(self):
        s = settings.load()
        self.assertIsNone(s["backend"])
        self.assertEqual(settings.model_for(s, "hermes", "translate"), "gpt-6-luna")
        self.assertEqual(settings.model_for(s, "hermes", "chat"), "gpt-6.1-sol")
        self.assertEqual(
            settings.model_for(s, "claude", "translate"), "claude-haiku-5-5"
        )
        self.assertEqual(settings.model_for(s, "claude", "summary"), "sonnet")
        self.assertEqual(settings.concurrency_for(s, "claude"), 8)

    def test_save_round_trip_keeps_other_backend(self):
        settings.save("hermes", {"translate": "gpt-5.6-luna"}, 4)
        settings.save("claude", {"chat": "opus"}, 2)
        s = settings.load()
        self.assertEqual(s["backend"], "claude")
        self.assertEqual(settings.model_for(s, "hermes", "translate"), "gpt-5.6-luna")
        self.assertEqual(settings.concurrency_for(s, "hermes"), 4)
        self.assertEqual(settings.model_for(s, "claude", "chat"), "opus")

    def test_corrupt_settings_fall_back_to_defaults(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(settings.load()["backend"])
        self.path.write_text(
            json.dumps(
                {
                    "backend": "openai",
                    "models": {"hermes": {"bogus": "x", "chat": 5}},
                    "concurrency": {"hermes": 999},
                }
            ),
            encoding="utf-8",
        )
        s = settings.load()
        self.assertIsNone(s["backend"])
        self.assertEqual(settings.model_for(s, "hermes", "chat"), "gpt-6.1-sol")
        self.assertNotIn("bogus", s["models"]["hermes"])
        self.assertEqual(settings.concurrency_for(s, "hermes"), 16)

    def test_env_overrides_backend(self):
        settings.save("claude", {}, 8)
        with patch.dict(os.environ, {"PAPER_READER_BACKEND": "hermes"}):
            self.assertEqual(settings.load()["backend"], "hermes")

    def test_save_rejects_unknown_backend_and_bad_values(self):
        with self.assertRaises(ValueError):
            settings.save("openai", {}, 8)
        with self.assertRaises(ValueError):
            settings.save("claude", {"chat": "bad model id!"}, 8)
        with self.assertRaises(ValueError):
            settings.save("claude", {}, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_settings -v`
Expected: FAIL with `ImportError: cannot import name 'settings' from 'app'`.

- [ ] **Step 3: Write the implementation**

`app/settings.py`:

```python
"""Paper Reader's AI settings: which AI Backend, a Model per Task, and concurrency.
Non-secret; credentials stay with each AI Backend's own login."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .store import DATA

BACKEND_NAMES = ("claude", "hermes")
TASKS = ("translate", "summary", "anatomy", "chat", "explain", "equation", "brief")
DEFAULT_CONCURRENCY = 8
MAX_CONCURRENCY = 16
_FAST = ("translate", "equation", "brief")
DEFAULT_MODELS = {
    "claude": {t: ("claude-haiku-5-5" if t in _FAST else "sonnet") for t in TASKS},
    "hermes": {t: ("gpt-6-luna" if t in _FAST else "gpt-6.1-sol") for t in TASKS},
}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}")


def settings_path() -> Path:
    return Path(
        os.environ.get("PAPER_READER_SETTINGS", str(DATA / "settings.json"))
    ).expanduser()


def _read() -> dict:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load() -> dict:
    raw = _read()
    backend = raw.get("backend") if raw.get("backend") in BACKEND_NAMES else None
    override = os.environ.get("PAPER_READER_BACKEND")
    if override in BACKEND_NAMES:
        backend = override
    models, concurrency = {}, {}
    raw_models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    raw_conc = (
        raw.get("concurrency") if isinstance(raw.get("concurrency"), dict) else {}
    )
    for name in BACKEND_NAMES:
        saved = raw_models.get(name) if isinstance(raw_models.get(name), dict) else {}
        models[name] = {
            t: saved[t]
            if isinstance(saved.get(t), str) and _MODEL_ID.fullmatch(saved[t])
            else DEFAULT_MODELS[name][t]
            for t in TASKS
        }
        n = raw_conc.get(name)
        concurrency[name] = (
            max(1, min(MAX_CONCURRENCY, n))
            if isinstance(n, int)
            else DEFAULT_CONCURRENCY
        )
    return {"backend": backend, "models": models, "concurrency": concurrency}


def model_for(settings: dict, backend: str, task: str) -> str:
    return settings["models"][backend][task]


def concurrency_for(settings: dict, backend: str) -> int:
    return settings["concurrency"][backend]


def save(backend: str, models: dict[str, str], concurrency: int) -> None:
    if backend not in BACKEND_NAMES:
        raise ValueError(f"Unknown AI Backend: {backend}")
    for task, model in models.items():
        if (
            task not in TASKS
            or not isinstance(model, str)
            or not _MODEL_ID.fullmatch(model)
        ):
            raise ValueError(f"Invalid model for {task}: {model!r}")
    if not isinstance(concurrency, int) or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"Concurrency must be 1-{MAX_CONCURRENCY}")
    current = load()
    raw = {
        "backend": backend,
        "models": current["models"],
        "concurrency": current["concurrency"],
    }
    raw["models"][backend] = {**current["models"][backend], **models}
    raw["concurrency"][backend] = concurrency
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(raw, tmp, indent=2)
    os.replace(tmp.name, path)
```

Note: when `PAPER_READER_BACKEND` is set, `save()` still writes the saved backend; `load()` keeps reporting the override.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_settings -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/settings.py tests/test_settings.py
git commit -m "Add AI settings store with a Model per Task and per-backend concurrency"
```

---

### Task 3: Claude Code backend

**Files:**
- Create: `app/backends/claude.py`, `tests/test_backend_claude.py`
- Modify: `app/backends/__init__.py` (register `claude`)

**Interfaces:**
- Consumes: `run_process`, `stream_process` (Task 1); `AIError`, `from_text` (Task 1).
- Produces (module `app.backends.claude`):
  - `NAME = "claude"`, `LABEL = "Claude Code"`, `MODEL_SUGGESTIONS: list[str]`
  - `installed() -> bool`
  - `describe() -> dict` → `{"provider": None}`
  - `async run(prompt: str, *, model: str, system: str | None = None, images: list[bytes] = ()) -> str` (final text)
  - `stream(prompt: str, *, model: str, system: str | None = None) -> AsyncIterator[str]` (text deltas; raises `AIError` if no successful `result` event arrives)

- [ ] **Step 1: Write the failing tests**

`tests/test_backend_claude.py`:

```python
import base64
import json
import os
import unittest
from unittest.mock import patch

from app.backends import claude
from app.errors import AIError, RateLimitError


def result_line(text="ok", is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": text}) + "\n"


def delta_line(text):
    event = {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": text},
    }
    return json.dumps({"type": "stream_event", "event": event}) + "\n"


class ClaudeBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_disables_tools_strips_api_key_and_sends_images_inline(self):
        seen = {}

        async def fake(args, stdin, env, timeout=300):
            seen.update(args=args, stdin=stdin, env=env)
            return result_line("E=mc^2").encode()

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("app.backends.claude.run_process", new=fake),
        ):
            text = await claude.run(
                "read it", model="sonnet", system="sys", images=[b"PNG"]
            )
        self.assertEqual(text, "E=mc^2")
        args = seen["args"]
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotIn("Read", args)
        self.assertNotIn("--resume", args)
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        message = json.loads(seen["stdin"].decode())
        parts = message["message"]["content"]
        self.assertEqual(parts[0]["source"]["data"], base64.b64encode(b"PNG").decode())
        self.assertEqual(parts[-1], {"type": "text", "text": "read it"})

    async def test_run_error_result_is_classified(self):
        async def fake(args, stdin, env, timeout=300):
            return result_line("Claude AI usage limit reached", is_error=True).encode()

        with patch("app.backends.claude.run_process", new=fake):
            with self.assertRaises(RateLimitError):
                await claude.run("x", model="sonnet")

    async def test_stream_yields_deltas_and_requires_final_result(self):
        async def ok(args, stdin, env, timeout=300):
            yield delta_line("1\t가\n")
            yield result_line()

        async def cut(args, stdin, env, timeout=300):
            yield delta_line("1\t가\n")

        with patch("app.backends.claude.stream_process", new=ok):
            self.assertEqual(
                [d async for d in claude.stream("p", model="sonnet")], ["1\t가\n"]
            )
        with patch("app.backends.claude.stream_process", new=cut):
            with self.assertRaises(AIError):
                [d async for d in claude.stream("p", model="sonnet")]

    def test_default_model_is_not_passed_as_flag(self):
        self.assertNotIn("--model", claude._args("default", None, stream=False))
        self.assertIn("opus", claude._args("opus", None, stream=False))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_backend_claude -v`
Expected: FAIL with `ImportError: cannot import name 'claude' from 'app.backends'`.

- [ ] **Step 3: Write the implementation**

`app/backends/claude.py`:

```python
"""Claude Code transport, run under the user's subscription login (ADR 0002).

Input and output both use stream-json so a message can carry inline images; every agent
tool is disabled because prompts carry untrusted PDF content."""

from __future__ import annotations

import base64
import json
import os
import shutil

from ..errors import AIError, from_text
from .process import run_process, stream_process

NAME = "claude"
LABEL = "Claude Code"
MODEL_SUGGESTIONS = ["claude-haiku-5-5", "sonnet", "opus", "haiku", "default"]


def installed() -> bool:
    return shutil.which("claude") is not None


def describe() -> dict:
    return {"provider": None}


def _env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # subscription access only
    env.pop("CLAUDECODE", None)
    env["MAX_THINKING_TOKENS"] = (
        "0"  # roughly halves latency; no Task needs extended thinking
    )
    return env


def _args(model: str, system: str | None, *, stream: bool) -> list[str]:
    args = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--allowed-tools",
        "",
    ]
    if stream:
        args.append("--include-partial-messages")
    if model and model != "default":
        args += ["--model", model]
    if system:
        args += ["--append-system-prompt", system]
    return args


def _message(prompt: str, images) -> bytes:
    parts = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode(),
            },
        }
        for png in images
    ]
    parts.append({"type": "text", "text": prompt})
    msg = {"type": "user", "message": {"role": "user", "content": parts}}
    return (json.dumps(msg) + "\n").encode("utf-8")


def _result(obj: dict) -> str:
    text = obj.get("result") or ""
    if obj.get("is_error"):
        raise from_text(
            str(text) + str(obj.get("subtype", "")),
            "Claude Code could not complete the request",
        )
    return text


async def run(prompt, *, model, system=None, images=()):
    out = await run_process(
        _args(model, system, stream=False), _message(prompt, images), _env()
    )
    final = None
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "result":
            final = _result(obj)
    if final is None or not final.strip():
        raise AIError("Claude Code returned no final response")
    return final


async def stream(prompt, *, model, system=None):
    finished, emitted, final = False, False, ""
    async for raw in stream_process(
        _args(model, system, stream=True), _message(prompt, ()), _env()
    ):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("type") == "result":
            final, finished = _result(obj), True
            continue
        event = obj.get("event") or {}
        delta = event.get("delta") or {}
        if (
            obj.get("type") == "stream_event"
            and event.get("type") == "content_block_delta"
            and delta.get("type") == "text_delta"
        ):
            emitted = True
            yield delta.get("text", "")
    if not finished:
        raise AIError("Claude Code stream ended without a final result")
    if not emitted and final:
        yield final
```

Update `app/backends/__init__.py`:

```python
"""AI Backends. Each module exposes NAME, LABEL, MODEL_SUGGESTIONS, installed(), describe(),
run(), and stream(). Register a new AI Backend (e.g. OpenClaw) by importing it here and adding
it to BACKENDS; app/settings.py BACKEND_NAMES and DEFAULT_MODELS need the same name."""

from __future__ import annotations

from . import claude

BACKENDS = {claude.NAME: claude}


def get(name: str):
    return BACKENDS[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_backend_claude -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/backends/claude.py app/backends/__init__.py tests/test_backend_claude.py
git commit -m "Add Claude Code backend with inline images and tools disabled"
```

---

### Task 4: Hermes backend and runner

**Files:**
- Create: `app/backends/hermes.py`, `app/backends/hermes_runner.py`, `tests/test_backend_hermes.py`, `tests/test_hermes_runner.py`
- Modify: `app/backends/__init__.py` (register `hermes`)

**Interfaces:**
- Consumes: `stream_process` (Task 1); `AIError`, `RateLimitError`, `ModelUnavailableError` (Task 1).
- Produces (module `app.backends.hermes`): `NAME = "hermes"`, `LABEL = "Hermes"`, `MODEL_SUGGESTIONS`, `installed() -> bool`, `describe() -> dict` (`{"provider": str, "model": str}`), `async run(prompt, *, model, system=None, images=()) -> str`, `stream(prompt, *, model, system=None) -> AsyncIterator[str]`.
- Runner protocol (stdin one JSON object): `{"prompt": str, "model": str, "system": str | null, "images": [base64 png]}`. Stdout JSON lines: `{"type": "delta", "text": str}`, `{"type": "result", "text": str}`, `{"type": "error", "kind": "rate_limit" | "model_unavailable" | "other"}`. `--info` prints `{"model", "provider"}` from saved Hermes config.

- [ ] **Step 1: Write the failing tests**

`tests/test_hermes_runner.py`:

```python
import base64
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from app.backends import hermes_runner


def fake_sdk(calls, *, tools=None, failure=None):
    class Agent:
        def __init__(
            self,
            enabled_toolsets,
            skip_context_files,
            skip_memory,
            load_soul_identity,
            session_db,
            stream_delta_callback,
            **kw,
        ):
            calls["toolsets"] = enabled_toolsets
            calls["model"] = kw.get("model")
            self.tools = tools or []
            self.delta = stream_delta_callback

        def run_conversation(self, message, conversation_history=None):
            calls["message"] = message
            if failure:
                return {"failed": True, "error": failure}
            self.delta("답")
            return {"final_response": "답"}

        def close(self):
            calls["closed"] = True

    config = types.ModuleType("hermes_cli.config")
    config.load_config = lambda: {
        "model": {"default": "saved", "provider": "openai-codex"}
    }
    runtime = types.ModuleType("hermes_cli.runtime_provider")
    runtime.resolve_runtime_provider = lambda **kw: {"provider": "openai-codex"}
    agent = types.ModuleType("run_agent")
    agent.AIAgent = Agent
    return {
        "hermes_cli": types.ModuleType("hermes_cli"),
        "hermes_cli.config": config,
        "hermes_cli.runtime_provider": runtime,
        "run_agent": agent,
    }


def invoke(request, **kw):
    calls, out = {}, io.StringIO()
    with (
        patch.dict(sys.modules, fake_sdk(calls, **kw)),
        patch.dict(os.environ, {"HERMES_INFERENCE_MODEL": "stale"}, clear=True),
        patch.object(sys, "argv", ["runner"]),
        patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
        patch.object(sys, "stdout", out),
        patch.object(sys, "stderr", io.StringIO()),
    ):
        status = hermes_runner.main()
    return status, calls, [json.loads(line) for line in out.getvalue().splitlines()]


class RunnerTests(unittest.TestCase):
    def test_text_request_has_no_tools(self):
        status, calls, events = invoke({"prompt": "p", "model": "gpt-6-luna"})
        self.assertEqual(status, 0)
        self.assertEqual(calls["toolsets"], [])
        self.assertEqual(calls["model"], "gpt-6-luna")
        self.assertEqual([e["type"] for e in events], ["delta", "result"])

    def test_images_become_inline_content_parts(self):
        png = base64.b64encode(b"PNG").decode()
        _, calls, _ = invoke({"prompt": "read", "model": "m", "images": [png]})
        parts = calls["message"]
        self.assertEqual(parts[0], {"type": "text", "text": "read"})
        self.assertEqual(parts[1]["image_url"]["url"], "data:image/png;base64," + png)

    def test_unexpected_tool_fails_closed(self):
        status, calls, events = invoke(
            {"prompt": "p", "model": "m"}, tools=[{"name": "x"}]
        )
        self.assertEqual(status, 1)
        self.assertNotIn("message", calls)
        self.assertEqual(events[-1]["type"], "error")

    def test_failure_kind_is_classified_without_raw_text(self):
        _, _, events = invoke(
            {"prompt": "p", "model": "gpt-6.1-luna"},
            failure="The 'gpt-6.1-luna' model is not supported when using Codex",
        )
        self.assertEqual(events[-1], {"type": "error", "kind": "model_unavailable"})
        _, _, events = invoke(
            {"prompt": "p", "model": "m"}, failure="HTTP 429 rate limit"
        )
        self.assertEqual(events[-1], {"type": "error", "kind": "rate_limit"})

    def test_saved_config_beats_stale_env(self):
        calls = {}
        with (
            patch.dict(sys.modules, fake_sdk(calls)),
            patch.dict(os.environ, {"HERMES_INFERENCE_MODEL": "stale"}, clear=True),
        ):
            info = hermes_runner._config()
        self.assertEqual(info, {"model": "saved", "provider": "openai-codex"})
```

`tests/test_backend_hermes.py`:

```python
import json
import unittest
from unittest.mock import patch

from app.backends import hermes
from app.errors import AIError, ModelUnavailableError, RateLimitError


def lines(*events):
    async def fake(args, stdin, env, timeout=300):
        fake.stdin = json.loads(stdin)
        for e in events:
            yield json.dumps(e) + "\n"

    return fake


class HermesBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("app.backends.hermes._python", return_value="/hermes/python")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_stream_and_run(self):
        fake = lines({"type": "delta", "text": "가"}, {"type": "result", "text": "가"})
        with patch("app.backends.hermes.stream_process", new=fake):
            self.assertEqual([d async for d in hermes.stream("p", model="m")], ["가"])
            self.assertEqual(await hermes.run("p", model="m", images=[b"PNG"]), "가")
        self.assertEqual(fake.stdin["images"], ["UE5H"])

    async def test_error_kinds_map_to_error_classes(self):
        for kind, cls in (
            ("rate_limit", RateLimitError),
            ("model_unavailable", ModelUnavailableError),
            ("other", AIError),
        ):
            with patch(
                "app.backends.hermes.stream_process",
                new=lines({"type": "error", "kind": kind}),
            ):
                with self.assertRaises(cls):
                    await hermes.run("p", model="m")

    async def test_missing_result_is_error(self):
        with patch(
            "app.backends.hermes.stream_process",
            new=lines({"type": "delta", "text": "x"}),
        ):
            with self.assertRaises(AIError):
                await hermes.run("p", model="m")

    def test_env_drops_gateway_identity(self):
        with patch.dict(
            "os.environ",
            {
                "HERMES_SESSION_ID": "s",
                "HERMES_GATEWAY_X": "g",
                "PYTHONPATH": "/x",
                "HOME": "/h",
            },
            clear=True,
        ):
            env = hermes._env()
        self.assertEqual(env.get("HOME"), "/h")
        self.assertEqual(env["HERMES_SAFE_MODE"], "1")
        for name in ("HERMES_SESSION_ID", "HERMES_GATEWAY_X", "PYTHONPATH"):
            self.assertNotIn(name, env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_hermes_runner tests.test_backend_hermes -v`
Expected: FAIL with `ImportError: cannot import name 'hermes_runner'`.

- [ ] **Step 3: Write the implementation**

`app/backends/hermes_runner.py` (runs under Hermes's Python, never imported by the web server except in tests):

```python
"""Private JSON-lines bridge into the Hermes SDK. Run with Hermes's own Python.

stdin: {"prompt", "model", "system", "images": [base64 png]}
stdout: {"type": "delta"|"result"|"error", ...} lines. Never prints prompt text, PDF
content, tokens, or raw provider errors."""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
import sys

_REQUIRED = {
    "enabled_toolsets",
    "skip_context_files",
    "skip_memory",
    "load_soul_identity",
    "session_db",
    "stream_delta_callback",
}
_RATE = re.compile(r"\b429\b|rate.?limit|usage limit|too many requests|quota", re.I)
_MODEL = re.compile(
    r"model[^.]{0,80}not supported|model_not_found|invalid model|unknown model", re.I
)


def _config() -> dict:
    """The saved Hermes model settings; they win over stale gateway env vars."""
    from hermes_cli.config import load_config

    model = load_config().get("model") or {}
    if isinstance(model, str):
        model = {"default": model}
    name = (
        model.get("default")
        or model.get("model")
        or os.environ.get("HERMES_INFERENCE_MODEL")
    )
    provider = (
        model.get("provider") or os.environ.get("HERMES_INFERENCE_PROVIDER") or "auto"
    )
    if not name:
        raise RuntimeError("No Hermes model configured; run hermes setup")
    return {"model": name, "provider": provider}


def _kind(text: str) -> str:
    if _RATE.search(text or ""):
        return "rate_limit"
    if _MODEL.search(text or ""):
        return "model_unavailable"
    return "other"


def main() -> int:
    os.environ["HERMES_SAFE_MODE"] = "1"
    output = sys.stdout

    def emit(event):
        output.write(json.dumps(event, ensure_ascii=False) + "\n")
        output.flush()

    agent = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            info = _config()
            if "--info" in sys.argv[1:]:
                emit(info)
                return 0
            from hermes_cli.runtime_provider import resolve_runtime_provider
            from run_agent import AIAgent

            signature = inspect.signature(AIAgent.__init__)
            if not _REQUIRED.issubset(signature.parameters):
                raise RuntimeError("Update Hermes: text-only SDK controls are missing")
            request = json.load(sys.stdin)
            model = request.get("model") or info["model"]
            runtime = resolve_runtime_provider(
                requested=info["provider"], target_model=model
            )
            kwargs = dict(
                model=model,
                api_key=runtime.get("api_key"),
                base_url=runtime.get("base_url"),
                provider=runtime.get("provider"),
                enabled_toolsets=[],
                max_iterations=2,
                quiet_mode=True,
                save_trajectories=False,
                verbose_logging=False,
                skip_context_files=True,
                load_soul_identity=False,
                skip_memory=True,
                session_db=None,
                stream_delta_callback=lambda text, *_: (
                    text and emit({"type": "delta", "text": text})
                ),
                ephemeral_system_prompt=request.get("system")
                or "Respond with text only.",
            )
            for name in (
                "api_mode",
                "credential_pool",
                "request_overrides",
                "service_tier",
            ):
                if name in signature.parameters and runtime.get(name) is not None:
                    kwargs[name] = runtime[name]
            agent = AIAgent(**kwargs)
            if getattr(agent, "tools", None):
                raise RuntimeError("Refusing paper input: Hermes enabled tools")
            agent._persist_disabled = True
            agent._session_json_enabled = False
            images = request.get("images") or []
            message = request["prompt"]
            if images:
                message = [{"type": "text", "text": request["prompt"]}] + [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + b64},
                    }
                    for b64 in images
                ]
            result = agent.run_conversation(message)
            text = result.get("final_response") or ""
            if result.get("failed") or result.get("partial") or not text.strip():
                emit({"type": "error", "kind": _kind(str(result.get("error") or ""))})
                return 1
            emit({"type": "result", "text": text})
            return 0
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "kind": _kind(str(exc))})
        return 1
    finally:
        if agent is not None:
            with contextlib.redirect_stdout(sys.stderr):
                agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

`app/backends/hermes.py`:

```python
"""Hermes transport. Runs hermes_runner.py with Hermes's own Python (its dependency tree is
separate from ours). No Hermes session, memory, or profile is created or changed."""

from __future__ import annotations

import base64
import functools
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..errors import AIError, ModelUnavailableError, RateLimitError
from .process import stream_process

NAME = "hermes"
LABEL = "Hermes"
MODEL_SUGGESTIONS = [
    "gpt-6-luna",
    "gpt-6.1-sol",
    "gpt-5.6-luna",
    "gpt-6-sol",
    "gpt-6-astra",
]
RUNNER = Path(__file__).with_name("hermes_runner.py")
_ERRORS = {
    "rate_limit": RateLimitError("Subscription usage limit reached; try again later"),
    "model_unavailable": ModelUnavailableError(
        "This model is not available for your account on this AI Backend"
    ),
}


def installed() -> bool:
    return bool(os.environ.get("HERMES_PYTHON") or shutil.which("hermes"))


def _python() -> str:
    override = os.environ.get("HERMES_PYTHON")
    if override:
        return override
    exe = shutil.which("hermes")
    if not exe:
        raise AIError("Hermes is not installed; install it and run hermes setup")
    path = Path(exe).resolve()
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        first = ""
    if first.startswith("#!") and "python" in first:
        return shlex.split(first[2:])[0]
    for candidate in (path.parent / "python3", path.parent / "python"):
        if candidate.is_file():
            return str(candidate)
    raise AIError(
        "Could not locate Hermes Python; set HERMES_PYTHON to its venv python"
    )


def _env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("HERMES_GATEWAY_", "HERMES_KANBAN_", "HERMES_PARENT_", "HERMES_SESSION")
        )
    }
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "_HERMES_GATEWAY",
        "HERMES_EPHEMERAL_SYSTEM_PROMPT",
        "HERMES_PREFILL_MESSAGES_FILE",
        "HERMES_INTERACTIVE",
        "HERMES_YOLO_MODE",
        "HERMES_ACCEPT_HOOKS",
    ):
        env.pop(name, None)
    env["HERMES_SAFE_MODE"] = "1"
    return env


def describe() -> dict:
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "config.yaml"
    stamp = home.stat().st_mtime_ns if home.exists() else 0
    return _describe(_python(), stamp)


@functools.lru_cache(maxsize=4)
def _describe(python: str, stamp: int) -> dict:
    try:
        out = subprocess.run(
            [python, str(RUNNER), "--info"],
            capture_output=True,
            env=_env(),
            timeout=30,
            check=False,
        )
        return json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise AIError("Could not read Hermes configuration; run hermes setup") from exc


async def _events(prompt, model, system, images):
    payload = {
        "prompt": prompt,
        "model": model,
        "system": system,
        "images": [base64.b64encode(png).decode() for png in images],
    }
    async for line in stream_process(
        [_python(), str(RUNNER)], json.dumps(payload).encode("utf-8"), _env()
    ):
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise AIError("Hermes returned an invalid message") from exc
        if event.get("type") == "error":
            raise _ERRORS.get(
                event.get("kind"),
                AIError("Hermes request failed; check login and model"),
            )
        yield event


async def run(prompt, *, model, system=None, images=()):
    result = None
    events = _events(prompt, model, system, images)
    try:
        async for event in events:
            if event.get("type") == "result":
                result = event.get("text", "")
    finally:
        await events.aclose()
    if not (result or "").strip():
        raise AIError("Hermes returned no final response")
    return result


async def stream(prompt, *, model, system=None):
    result, emitted = None, False
    events = _events(prompt, model, system, ())
    try:
        async for event in events:
            if event.get("type") == "delta":
                emitted = True
                yield event["text"]
            elif event.get("type") == "result":
                result = event.get("text", "")
    finally:
        await events.aclose()
    if result is None:
        raise AIError("Hermes stream ended without a final response")
    if not emitted:
        yield result
```

Note: `stream_process` raises on a non-zero exit; the runner exits 1 after an error line, and `_events` raises from the error line first, so the classified error wins.

Update `app/backends/__init__.py` imports and registry:

```python
from . import claude, hermes

BACKENDS = {claude.NAME: claude, hermes.NAME: hermes}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_hermes_runner tests.test_backend_hermes -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Smoke test against the real Hermes (manual, not in CI)**

Run:
```bash
printf '%s' '{"prompt":"Reply with: ok","model":"gpt-6-luna"}' | HERMES_SAFE_MODE=1 \
  ~/.hermes/hermes-agent/venv/bin/python3 app/backends/hermes_runner.py
```
Expected: a `delta` line and a `{"type": "result", "text": "ok"}` line.

- [ ] **Step 6: Commit**

```bash
git add app/backends/hermes.py app/backends/hermes_runner.py app/backends/__init__.py tests/test_backend_hermes.py tests/test_hermes_runner.py
git commit -m "Add Hermes backend with inline images and classified errors"
```

---

### Task 5: `app/ai.py` — prompts, Task models, concurrency

**Files:**
- Create: `app/ai.py` (move all prompt constants and prompt-building functions from `app/claude.py`)
- Delete: `app/claude.py`
- Create: `tests/test_ai.py`
- Modify: `GLOSSARY.md` (add "explanation" to the Task definition)

**Interfaces:**
- Consumes: `app.settings.load/model_for/concurrency_for` (Task 2); `app.backends.get/BACKENDS` (Tasks 3–4); errors (Task 1).
- Produces (module `app.ai`):
  - `current_backend() -> str` (raises `AIError("No AI Backend selected; open Settings")` when `settings.load()["backend"]` is None)
  - `model(task: str) -> str`
  - `cache_tag(task: str) -> str` → `"<backend>-<model>"` with any char outside `[A-Za-z0-9._-]` replaced by `_`
  - `async probe(backend: str, model: str) -> None` (raises the backend's error)
  - `async summarize(paper_text) -> str`, `async explain(selection) -> str`, `async anatomy(paper_text) -> dict`, `async translate(text, target_lang) -> str`, `async translate_segments(segments, target_lang, brief="") -> list[str] | None`, `async translate_brief(front_matter, target_lang) -> str`, `async equation_latex(png: bytes) -> str`, `async chat(question, *, paper_text, history: list[dict]) -> str`
  - `translate_stream(segments, target_lang, brief="") -> AsyncIterator[tuple[int | None, str]]` (main's tolerant `(index, text)` contract)
  - `verify_quote(quote, paper_text) -> bool` (unchanged)
  - None of these take a `model` argument any more; the Model comes from settings per Task.

- [ ] **Step 1: Write the failing tests**

`tests/test_ai.py`:

```python
import asyncio
import unittest
from unittest.mock import patch

from app import ai
from app.errors import AIError, RateLimitError


def settings_for(backend="hermes", concurrency=8):
    from app import settings

    s = settings.load()
    s["backend"] = backend
    s["concurrency"][backend] = concurrency
    return s


class FakeBackend:
    NAME = "hermes"

    def __init__(self, text="ok", deltas=(), error=None):
        self.calls, self.text, self.deltas, self.error = [], text, deltas, error
        self.active = self.peak = 0

    async def run(self, prompt, *, model, system=None, images=()):
        self.calls.append(
            dict(prompt=prompt, model=model, system=system, images=list(images))
        )
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if self.error:
            raise self.error
        return self.text

    async def stream(self, prompt, *, model, system=None):
        self.calls.append(dict(prompt=prompt, model=model, system=system))
        for d in self.deltas:
            yield d
        if self.error:
            raise self.error


class AiTests(unittest.IsolatedAsyncioTestCase):
    def use(self, backend, s=None):
        p1 = patch("app.ai.settings.load", return_value=s or settings_for())
        p2 = patch("app.ai.backends.get", return_value=backend)
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    async def test_each_task_uses_its_own_model(self):
        fake = FakeBackend(text="summary")
        self.use(fake)
        await ai.summarize("paper")
        await ai.translate("text", "Korean")
        self.assertEqual(
            [c["model"] for c in fake.calls], ["gpt-6.1-sol", "gpt-6-luna"]
        )
        self.assertIn("NOT instructions", fake.calls[0]["prompt"])

    async def test_no_backend_selected_is_a_clear_error(self):
        s = settings_for()
        s["backend"] = None
        with patch("app.ai.settings.load", return_value=s):
            with self.assertRaises(AIError):
                await ai.summarize("paper")

    async def test_concurrency_is_bounded_per_backend(self):
        fake = FakeBackend()
        self.use(fake, settings_for(concurrency=2))
        await asyncio.gather(*(ai.explain("x") for _ in range(6)))
        self.assertEqual(fake.peak, 2)

    async def test_translate_stream_maps_by_model_index_and_tolerates_gaps(self):
        fake = FakeBackend(deltas=["1\t가\n3", "\t다\n", "no index line\n"])
        self.use(fake)
        out = [x async for x in ai.translate_stream(["a", "b", "c"], "Korean")]
        self.assertEqual(out, [(0, "가"), (2, "다"), (None, "no index line")])

    async def test_rate_limit_in_stream_is_reported_not_retried(self):
        fake = FakeBackend(deltas=["1\t가\n"], error=RateLimitError("limit"))
        self.use(fake)
        got = []
        with self.assertRaises(RateLimitError):
            async for item in ai.translate_stream(["a", "b"], "Korean"):
                got.append(item)
        self.assertEqual(got, [(0, "가")])
        self.assertEqual(len(fake.calls), 1)

    async def test_equation_sends_image_inline_and_strips_fences(self):
        fake = FakeBackend(text="```latex\n\\[ E=mc^2 \\]\n```")
        self.use(fake)
        self.assertEqual(await ai.equation_latex(b"PNG"), "E=mc^2")
        self.assertEqual(fake.calls[0]["images"], [b"PNG"])
        self.assertEqual(fake.calls[0]["model"], "gpt-6-luna")

    async def test_chat_sends_paper_and_transcript(self):
        fake = FakeBackend(text="answer")
        self.use(fake)
        history = [{"role": "user", "text": "Q1"}, {"role": "assistant", "text": "A1"}]
        self.assertEqual(
            await ai.chat("Q2", paper_text="PAPER", history=history), "answer"
        )
        prompt = fake.calls[0]["prompt"]
        for part in ("PAPER", "Q1", "A1", "Q2"):
            self.assertIn(part, prompt)
        self.assertLess(prompt.index("A1"), prompt.index("Q2"))

    def test_cache_tag_is_per_backend_and_model_and_filename_safe(self):
        with patch("app.ai.settings.load", return_value=settings_for("hermes")):
            hermes_tag = ai.cache_tag("translate")
        with patch("app.ai.settings.load", return_value=settings_for("claude")):
            claude_tag = ai.cache_tag("translate")
        self.assertEqual(hermes_tag, "hermes-gpt-6-luna")
        self.assertEqual(claude_tag, "claude-claude-haiku-5-5")
        s = settings_for("hermes")
        s["models"]["hermes"]["summary"] = "openai/gpt:x"
        with patch("app.ai.settings.load", return_value=s):
            self.assertEqual(ai.cache_tag("summary"), "hermes-openai_gpt_x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_ai -v`
Expected: FAIL with `ImportError: cannot import name 'ai' from 'app'`.

- [ ] **Step 3: Create `app/ai.py`**

Start the file by moving these unchanged from `app/claude.py`: `_NO_TOOLS`, `_SUMMARY_SYS`, `_TRANSLATE_SYS`, `_CHAT_SYS`, `_EXPLAIN_SYS`, `_ANATOMY_SYS`, `_BRIEF_SYS`, `_MATH_SYS`, `_UNTRUSTED_HEADER`, `_paper`, `_parse_json_array`, `_brief_block`, `_parse_json_object`, `_ANATOMY_SCHEMA`, `verify_quote`. Then add this header and these functions (they replace every subprocess call in `app/claude.py`):

```python
"""Backend-neutral prompts for every Task. The AI Backend comes from settings, and so does
the Model for each Task; transport lives in app/backends/."""

from __future__ import annotations

import asyncio
import json
import re

from . import backends, settings
from .errors import AIError

_limits: dict[tuple, asyncio.Semaphore] = {}


def current_backend() -> str:
    name = settings.load()["backend"]
    if name is None:
        raise AIError("No AI Backend selected; open Settings")
    return name


def model(task: str) -> str:
    return settings.model_for(settings.load(), current_backend(), task)


def cache_tag(task: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{current_backend()}-{model(task)}")


def _limit(name: str) -> asyncio.Semaphore:
    n = settings.concurrency_for(settings.load(), name)
    key = (asyncio.get_running_loop(), name, n)
    if key not in _limits:
        _limits[key] = asyncio.Semaphore(n)
    return _limits[key]


async def _run(task: str, prompt: str, system: str | None, images=()) -> str:
    name = current_backend()
    async with _limit(name):
        return await backends.get(name).run(
            prompt, model=model(task), system=system, images=images
        )


async def probe(backend: str, model_id: str) -> None:
    await backends.get(backend).run("Reply with: ok", model=model_id, system=None)


async def summarize(paper_text: str) -> str:
    prompt = (
        "Summarize the academic paper below. Use this exact markdown structure:\n\n"
        "## TL;DR\n(2-3 sentences)\n\n"
        "## Key Contributions\n(bullet points)\n\n"
        "## Method\n\n## Results\n\n## Limitations\n\n"
        "Be concise and precise. Write in English.\n\n" + _paper(paper_text)
    )
    return await _run("summary", prompt, _SUMMARY_SYS)


async def translate(text: str, target_lang: str) -> str:
    prompt = f"Translate the following text into {target_lang}.\n\n---\n{text}"
    return await _run("translate", prompt, _TRANSLATE_SYS)


async def explain(selection: str) -> str:
    prompt = (
        "Explain the following excerpt from a research paper in simple, precise terms "
        "(2-5 sentences). Define any jargon.\n\n---\n" + selection
    )
    return await _run("explain", prompt, _EXPLAIN_SYS)


async def translate_brief(front_matter: str, target_lang: str) -> str:
    prompt = (
        f"Read the paper front matter below and produce a concise translation brief for a "
        f"translator rendering it into {target_lang}. Use this exact plain-text form:\n"
        f"FIELD: <one line>\n"
        f"ABBREVIATIONS: <ABBR = expansion; ...> (only those the paper defines; omit the "
        f"line if none)\n"
        f"GLOSSARY: <English term -> {target_lang} rendering; ...> (key technical terms, "
        f"at most 20)\n"
        f"Keep it short. No commentary, no markdown.\n\n" + _paper(front_matter)
    )
    return (await _run("brief", prompt, _BRIEF_SYS)).strip()


async def translate_segments(
    segments: list[str], target_lang: str, brief: str = ""
) -> list[str] | None:
    if not segments:
        return []
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments))
    prompt = (
        _brief_block(brief)
        + f"Translate each numbered segment below into {target_lang}. "
        f"Return ONLY a JSON array of exactly {len(segments)} strings — the translations "
        f"in the same order, one per input segment. No numbering inside the strings, no "
        f"commentary, no markdown fences.\n\n" + numbered
    )
    arr = _parse_json_array(await _run("translate", prompt, _TRANSLATE_SYS))
    return arr if arr is not None and len(arr) == len(segments) else None


def _parse_line(line: str) -> tuple[int | None, str]:
    """'<index><TAB><text>' -> (0-based index, text); index None when the line has none."""
    if "\t" in line:
        head, text = line.split("\t", 1)
        head = head.strip()
        return (int(head) - 1 if head.isdigit() else None), text.strip()
    m = re.match(r"(\d+)[.)]\s+(.*)$", line)
    if m:
        return int(m.group(1)) - 1, m.group(2).strip()
    return None, line.strip()


async def translate_stream(segments: list[str], target_lang: str, brief: str = ""):
    """Yield (index, translation) per line as the model streams. Mapping by the model's index
    keeps a dropped or merged line from shifting later segments."""
    if not segments:
        return
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments))
    prompt = (
        _brief_block(brief)
        + f"Translate each numbered segment into {target_lang}. Output ONE line per segment "
        f"in the exact form `<index><TAB><translation>` (a real tab between index and text), "
        f"in the same order, index starting at 1. No JSON, no commentary, no blank lines.\n\n"
        f"{_UNTRUSTED_HEADER}<segments>\n{numbered}\n</segments>"
    )
    name = current_backend()
    buf = ""
    async with _limit(name):
        chunks = backends.get(name).stream(
            prompt, model=model("translate"), system=_TRANSLATE_SYS
        )
        try:
            async for delta in chunks:
                buf += delta
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.strip():
                        yield _parse_line(line.strip())
        finally:
            await chunks.aclose()
    if buf.strip():
        yield _parse_line(buf.strip())


async def equation_latex(png: bytes) -> str:
    """Transcribe one display equation from its rendered crop. The image travels inline in the
    message with every tool disabled (ADR 0002)."""
    latex = (
        await _run(
            "equation",
            "Transcribe the equation in this image as LaTeX.",
            _MATH_SYS,
            images=[png],
        )
    ).strip()
    latex = re.sub(r"^```(?:latex)?\s*|\s*```$", "", latex).strip()
    if latex.startswith("\\[") and latex.endswith("\\]"):
        latex = latex[2:-2].strip()
    return latex.strip("$").strip()


_MAX_TURNS = 20


async def chat(question: str, *, paper_text: str, history: list[dict]) -> str:
    """Stateless Q&A: every call carries the paper and the recent transcript, so the same
    Chat History continues on any AI Backend."""
    turns = "\n\n".join(
        f"{'USER' if t['role'] == 'user' else 'ASSISTANT'}: {t['text']}"
        for t in history[-_MAX_TURNS:]
    )
    prompt = (
        "Answer my questions using only the paper below. Ground each claim in the paper's "
        "wording.\n\n"
        + _paper(paper_text)
        + (f"\n\nCONVERSATION SO FAR:\n{turns}" if turns else "")
        + f"\n\nQUESTION: {question}"
    )
    return await _run("chat", prompt, _CHAT_SYS)


async def anatomy(paper_text: str) -> dict:
    prompt = (
        "Analyze the paper below into a 'Research Anatomy'. Return ONLY JSON matching "
        "this shape (no prose, no markdown fences):\n" + _ANATOMY_SCHEMA + "\n\n"
        "Rules: write point text in Korean but keep technical terms/metrics in their "
        "source language. Each point makes ONE atomic claim. Label kind as author_stated "
        "(the paper states it), synthesis (combines multiple parts), or ai_interpretation. "
        "`quote` must be a short VERBATIM span copied from the paper that supports the "
        "point, or null. If a card does not apply, set status=not_present with an empty "
        "points list. Never invent numbers, datasets, baselines, ablations, or limitations.\n\n"
        + _paper(paper_text)
    )
    text = await _run("anatomy", prompt, _ANATOMY_SYS)
    obj = _parse_json_object(text)
    if obj is None:
        repair = (
            "Your previous output was not valid JSON. Return ONLY the JSON object, "
            "matching this shape, nothing else:\n" + _ANATOMY_SCHEMA + "\n\n"
            "Previous output:\n" + text[:6000]
        )
        obj = _parse_json_object(await _run("anatomy", repair, _ANATOMY_SYS))
    if obj is None:
        raise AIError("Could not parse anatomy JSON")
    return obj
```

Delete `app/claude.py` (`git rm app/claude.py`). `app/main.py` still imports it until Task 6; run only `tests.test_ai` here.

In `GLOSSARY.md`, change the Task definition's list to: "translation, summary, anatomy, chat, explanation, equation reading, or glossary building".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_ai -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/ai.py tests/test_ai.py GLOSSARY.md
git rm app/claude.py
git commit -m "Move prompts to backend-neutral app/ai.py with a Model per Task"
```

---

### Task 6: Server wiring — caches, errors, chat, config, settings

**Files:**
- Modify: `app/main.py`, `app/store.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: everything in `app.ai` (Task 5), `app.settings` (Task 2), `app.backends.BACKENDS` (Tasks 3–4), errors (Task 1).
- Produces (HTTP):
  - `GET /api/config` → `{"backend": str|null, "reason": str|null, "backends": [{"id","label","installed","provider","suggestions"}], "tasks": [..], "models": {backend: {task: model}}, "concurrency": {backend: int}}`
  - `POST /api/settings` body `{"backend": str, "models": {task: model}, "concurrency": int}` → 200 `{"ok": true}` or 400 `{"detail": "<model>: <reason>"}`
  - `GET /api/chat/{doc_id}` → `{"turns": [{"role", "text"}]}`; `POST /api/chat/{doc_id}/reset` → `{"ok": true}`
  - Any `RateLimitError` → HTTP 429 `{"detail", "rate_limited": true}`; other `AIError` → 502 `{"detail"}`
  - `/api/translate_stream` error line: `{"error": str, "rate_limited": bool}`
  - Request bodies no longer carry `model`; the server ignores one if sent.
- Produces (`app.store`): `chat_get(doc_id) -> list[dict]`, `chat_put(doc_id, turns: list[dict]) -> None`, `chat_clear(doc_id) -> None`, stored in `data/chats/<doc_id>.json`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:

```python
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main, settings, store
from app.errors import ModelUnavailableError, RateLimitError

DOC = {"pages": ["PAPER TEXT"], "meta": {"num_pages": 1, "title": "T"}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.patches = [
            patch.dict(
                os.environ, {"PAPER_READER_SETTINGS": str(root / "settings.json")}
            ),
            patch.object(store, "CACHE", root / "cache"),
            patch.object(store, "CHATS", root / "chats"),
            patch("app.main._require_doc", return_value=DOC),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("PAPER_READER_BACKEND", None)
        settings.save("hermes", {}, 8)
        self.client = TestClient(
            main.app
        )  # not a context manager: skips the model warm-up

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.dir.cleanup()

    def test_config_with_no_backend_installed(self):
        settings.settings_path().unlink()
        with (
            patch("app.backends.claude.installed", return_value=False),
            patch("app.backends.hermes.installed", return_value=False),
        ):
            body = self.client.get("/api/config").json()
        self.assertIsNone(body["backend"])
        self.assertIn("install", body["reason"].lower())
        self.assertEqual({b["id"] for b in body["backends"]}, {"claude", "hermes"})

    def test_config_picks_the_only_installed_backend_after_a_probe(self):
        settings.settings_path().unlink()
        with (
            patch("app.backends.claude.installed", return_value=True),
            patch("app.backends.hermes.installed", return_value=False),
            patch("app.main.ai.probe", new=AsyncMock()) as probe,
        ):
            body = self.client.get("/api/config").json()
        self.assertEqual(body["backend"], "claude")
        probe.assert_awaited_once()
        self.assertEqual(settings.load()["backend"], "claude")

    def test_settings_save_rejects_unavailable_model(self):
        error = ModelUnavailableError(
            "This model is not available for your account on this AI Backend"
        )
        with patch("app.main.ai.probe", new=AsyncMock(side_effect=error)):
            r = self.client.post(
                "/api/settings",
                json={
                    "backend": "hermes",
                    "models": {"translate": "gpt-6.1-luna"},
                    "concurrency": 8,
                },
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("gpt-6.1-luna", r.json()["detail"])
        self.assertEqual(
            settings.model_for(settings.load(), "hermes", "translate"), "gpt-6-luna"
        )

    def test_settings_save_probes_each_distinct_model_once(self):
        with patch("app.main.ai.probe", new=AsyncMock()) as probe:
            r = self.client.post(
                "/api/settings",
                json={
                    "backend": "claude",
                    "models": {"chat": "opus"},
                    "concurrency": 4,
                },
            )
        self.assertEqual(r.status_code, 200)
        probed = sorted(call.args[1] for call in probe.await_args_list)
        self.assertEqual(probed, ["claude-haiku-5-5", "opus", "sonnet"])
        self.assertEqual(settings.load()["backend"], "claude")

    def test_cache_keys_differ_per_backend(self):
        with patch("app.main.ai.summarize", new=AsyncMock(return_value="S")):
            self.client.post("/api/summarize", json={"doc_id": "d"})
            settings.save("claude", {}, 8)
            self.client.post("/api/summarize", json={"doc_id": "d"})
        names = sorted(p.name for p in (store.CACHE / "d").iterdir())
        self.assertEqual(
            names, ["summary_claude-sonnet.json", "summary_hermes-gpt-6.1-sol.json"]
        )

    def test_chat_history_survives_backend_switch(self):
        answers = AsyncMock(side_effect=["A1", "A2"])
        with patch("app.main.ai.chat", new=answers):
            self.client.post("/api/chat", json={"doc_id": "d", "question": "Q1"})
            settings.save("claude", {}, 8)
            self.client.post("/api/chat", json={"doc_id": "d", "question": "Q2"})
        second = answers.await_args_list[1]
        self.assertEqual(
            second.kwargs["history"],
            [{"role": "user", "text": "Q1"}, {"role": "assistant", "text": "A1"}],
        )
        turns = self.client.get("/api/chat/d").json()["turns"]
        self.assertEqual([t["text"] for t in turns], ["Q1", "A1", "Q2", "A2"])
        self.client.post("/api/chat/d/reset")
        self.assertEqual(self.client.get("/api/chat/d").json()["turns"], [])

    def test_rate_limit_maps_to_429(self):
        with patch(
            "app.main.ai.summarize", new=AsyncMock(side_effect=RateLimitError("limit"))
        ):
            r = self.client.post("/api/summarize", json={"doc_id": "d"})
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r.json()["rate_limited"])

    def test_stream_rate_limit_line_and_no_cache(self):
        async def stream(segs, lang, brief=""):
            yield (0, "가")
            raise RateLimitError("limit")

        with (
            patch("app.main.ai.translate_stream", new=stream),
            patch("app.main._get_brief", new=AsyncMock(return_value="")),
        ):
            r = self.client.post(
                "/api/translate_stream",
                json={"doc_id": "d", "page": 0, "segments": ["a", "b"]},
            )
        lines = [json.loads(x) for x in r.text.splitlines()]
        self.assertEqual(lines[0], {"i": 0, "tr": "가"})
        self.assertEqual(lines[-1]["rate_limited"], True)
        self.assertFalse(
            (store.CACHE / "d").exists()
            and any(p.name.startswith("tr_") for p in (store.CACHE / "d").iterdir())
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_api -v`
Expected: FAIL with `ImportError: cannot import name 'claude' from 'app'` (main still imports the deleted module).

- [ ] **Step 3: Add chat storage to `app/store.py`**

Add below `CACHE = DATA / "cache"`:

```python
CHATS = DATA / "chats"
```

Add at the end of the file:

```python
def chat_get(doc_id: str) -> list[dict]:
    p = CHATS / f"{doc_id}.json"
    try:
        turns = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return turns if isinstance(turns, list) else []


def chat_put(doc_id: str, turns: list[dict]) -> None:
    CHATS.mkdir(parents=True, exist_ok=True)
    (CHATS / f"{doc_id}.json").write_text(
        json.dumps(turns, ensure_ascii=False), "utf-8"
    )


def chat_clear(doc_id: str) -> None:
    (CHATS / f"{doc_id}.json").unlink(missing_ok=True)
```

- [ ] **Step 4: Rewire `app/main.py`**

1. Imports: replace `from . import claude, layout_model, pdf_utils, store` with:

```python
from fastapi import Request
from fastapi.responses import JSONResponse

from . import ai, backends, layout_model, pdf_utils, settings, store
from .errors import AIError, RateLimitError
```

2. Delete `_chat_sessions` and its comment. Add after `app = FastAPI(...)`:

```python
@app.exception_handler(AIError)
async def _ai_error(_request: Request, exc: AIError):
    limited = isinstance(exc, RateLimitError)
    return JSONResponse(
        status_code=429 if limited else 502,
        content={"detail": str(exc), "rate_limited": limited},
    )
```

3. Remove every `model: str = claude.DEFAULT_MODEL` field from `SummarizeReq`, `AnatomyReq`, `TranslateReq`, `ExplainReq`, `ChatReq` (add `model_config = {"extra": "ignore"}` is the pydantic default, nothing to add). Remove `model = claude.normalize_model(...)` lines.

4. Cache keys: `summary_{model}` → `f"summary_{ai.cache_tag('summary')}"`; `anatomy_{model}` → `f"anatomy_{ai.cache_tag('anatomy')}"`; both `tr_{req.target_lang}_{model}_...` → `f"tr_{req.target_lang}_{ai.cache_tag('translate')}_{req.page}_{sig}"`; `brief_{target_lang}` → `f"brief_{target_lang}_{ai.cache_tag('brief')}"`; math key `f"math_v{_LAYOUT_VERSION}_{m}_{page}"` → `f"math_v{_LAYOUT_VERSION}_{ai.cache_tag('equation')}_{page}"`.

5. Calls: `claude.summarize(text, model=model)` → `ai.summarize(text)`; `claude.anatomy(text, model=model)` → `ai.anatomy(text)` and `except claude.ClaudeError` → `except AIError` re-raising as before only for non-rate-limit errors:

```python
    try:
        result = await ai.anatomy(text)
    except RateLimitError:
        raise
    except AIError as e:
        raise HTTPException(502, f"분석 실패: {e}") from e
```

`claude.verify_quote` → `ai.verify_quote`; `claude.translate_segments(segs, req.target_lang, model=model, brief=brief)` → `ai.translate_segments(segs, req.target_lang, brief=brief)`; `claude.translate(source, req.target_lang, model=model)` → `ai.translate(source, req.target_lang)`; `claude.explain(text[:4000], model=...)` → `ai.explain(text[:4000])`; in `_get_brief`, `claude.translate_brief(src, target_lang)` → `ai.translate_brief(src, target_lang)` and keep its `except Exception` but re-raise `RateLimitError` first:

```python
    except RateLimitError:
        raise
    except Exception:  # noqa: BLE001
        return ""
```

6. `/api/math`: drop the `model` query parameter and the temp file. Replace the per-equation block with:

```python
        png = pdf_utils.crop_region_png(pdf_bytes, page, bbox)
        try:
            latex = await ai.equation_latex(png)
        except RateLimitError:
            raise
        except AIError:
            latex = ""  # best-effort: keep the original text if a crop can't be read
```

and remove `import tempfile` if unused.

7. `/api/translate_stream` generator: wrap the loop so a backend error ends the stream with an error line and skips caching:

```python
collected: dict[int, str] = {}
nxt = 0
chunks = ai.translate_stream(segs, req.target_lang, brief=brief)
try:
    async for idx, tr in chunks:
        if idx is None:
            idx = nxt
        if not (0 <= idx < len(segs)) or idx in collected:
            continue
        nxt = idx + 1
        collected[idx] = tr
        yield json.dumps({"i": idx, "tr": tr}, ensure_ascii=False) + "\n"
except AIError as exc:
    yield (
        json.dumps(
            {"error": str(exc), "rate_limited": isinstance(exc, RateLimitError)},
            ensure_ascii=False,
        )
        + "\n"
    )
    return
finally:
    await chunks.aclose()
```

Also wrap `brief = await _get_brief(...)` inside the generator the same way (a `RateLimitError` there yields the error line and returns).

8. Chat, replacing the old handler:

```python
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
```

9. Config and settings, before the static mount:

```python
def _backend_rows() -> list[dict]:
    rows = []
    for name, mod in backends.BACKENDS.items():
        provider = None
        if mod.installed():
            try:
                provider = mod.describe().get("provider")
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
    rows = _backend_rows()
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
```

Note: `_first_run_backend` uses `settings.save(name, {}, ...)` so the probe result is remembered and later loads skip the probe.

- [ ] **Step 5: Run the whole suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS (all tests from Tasks 1–6).

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/store.py tests/test_api.py
git commit -m "Route every AI Task through the selected backend; add settings, chat history, 429"
```

---

### Task 7: UI — settings dialog, backend badge, concurrency, rate-limit stop, chat history

**Files:**
- Modify: `web/index.html`, `web/app.js`, `web/styles.css`
- Create: `tests/frontend_ratelimit.test.js`, `tests/test_frontend.py`

**Interfaces:**
- Consumes: `GET /api/config`, `POST /api/settings`, `GET /api/chat/{doc_id}`, `POST /api/chat/{doc_id}/reset`, stream error lines with `rate_limited` (Task 6).
- Produces (JS globals): `S.aiConfig` (the `/api/config` body), `S.rateLimited` (the `S.trToken` value that hit the limit, or `null`), `class RateLimited extends Error`, `loadConfig()`, `openSettings()`.

- [ ] **Step 1: Write the failing frontend test**

`tests/frontend_ratelimit.test.js` loads `translateBatch` and `streamTranslate` from `web/app.js` into a VM, feeds a stream whose last line is a rate-limit error, and checks the batch is not retried and its gaps are filled with source text:

```javascript
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const src = fs.readFileSync("web/app.js", "utf8");
const pick = (name, end) => src.slice(src.indexOf(name), src.indexOf(end, src.indexOf(name)));
const line = (name) => src.slice(src.indexOf(name), src.indexOf("\n", src.indexOf(name)));
const code = [
  line("class RateLimited"),
  pick("async function streamTranslate(", "\n}\n") + "\n}\n",
  pick("async function translateBatch(", "\n}\n") + "\n}\n",
].join("\n");

(async () => {
  let calls = 0;
  const page = { segs: [{ text: "S0" }, { text: "S1" }], trBuf: [], spanByK: [] };
  const context = {
    S: { docId: "d", trToken: 1, pages: [page], rateLimited: null },
    lang: () => "Korean", sleep: async () => {}, paintFrontier: () => {}, showRateLimit: () => {},
    TextDecoder, JSON, Error,
    fetch: async () => {
      calls++;
      return new Response('{"i":0,"tr":"가"}\n{"error":"limit","rate_limited":true}\n');
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  await context.translateBatch(0, { start: 0, texts: ["S0", "S1"] }, false, 1);
  assert.equal(calls, 1, "a rate-limited batch must not be retried");
  assert.deepEqual(page.trBuf, ["가", "S1"]);
  assert.equal(context.S.rateLimited, 1);
  console.log("rate-limit frontend check PASS");
})().catch((e) => { console.error(e); process.exitCode = 1; });
```

`tests/test_frontend.py`:

```python
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node is only needed for JS checks")
class FrontendTests(unittest.TestCase):
    def node(self, script):
        r = subprocess.run(
            ["node", script], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_syntax(self):
        r = subprocess.run(["node", "--check", "web/app.js"], cwd=ROOT)
        self.assertEqual(r.returncode, 0)

    def test_rate_limit_stops_batch(self):
        self.node("tests/frontend_ratelimit.test.js")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m unittest tests.test_frontend -v`
Expected: FAIL (`class RateLimited` not found, so the VM throws).

- [ ] **Step 3: Update `web/index.html`**

Replace the whole `<label class="ctl">Model … </label>` block with:

```html
    <button id="ai-badge" class="btn-ghost" type="button" title="AI settings">AI: …</button>
```

Add before `<main class="workspace">`:

```html
  <div id="ai-banner" class="ai-banner hidden" role="alert"></div>

  <dialog id="settings" class="settings">
    <form method="dialog" id="settings-form">
      <h3>AI settings</h3>
      <p id="settings-reason" class="settings-reason hidden"></p>
      <label>AI Backend <select id="set-backend"></select></label>
      <p id="set-provider" class="hint"></p>
      <div id="set-models"></div>
      <label>Parallel requests <input id="set-conc" type="number" min="1" max="16" /></label>
      <p id="settings-error" class="settings-error hidden"></p>
      <menu>
        <button value="cancel" type="button" id="settings-cancel">Cancel</button>
        <button id="settings-save" type="submit">Save</button>
      </menu>
    </form>
  </dialog>
```

In the chat tab, next to the chat form, add:

```html
          <button id="chat-reset" class="btn-ghost" type="button" title="Clear this paper's chat">대화 지우기</button>
```

- [ ] **Step 4: Update `web/styles.css`**

Append:

```css
.ai-banner { position: fixed; top: 60px; left: 50%; transform: translateX(-50%); z-index: 100;
  padding: 10px 16px; background: var(--panel2); color: var(--text);
  border: 1px solid var(--accent); border-radius: var(--radius); }
.settings { border: 1px solid var(--accent); border-radius: var(--radius);
  background: var(--panel2); color: var(--text); max-width: min(520px, calc(100vw - 32px)); }
.settings label { display: flex; justify-content: space-between; gap: 12px; margin: 8px 0; }
.settings input, .settings select { min-width: 220px; }
.settings-error { color: #f88; }
.settings-reason { color: var(--accent); }
```

- [ ] **Step 5: Update `web/app.js`**

1. Delete `const model = () => $("model").value;`. Add to `S`: `aiConfig: null, rateLimited: null,`.

2. Add after the `S` object:

```javascript
class RateLimited extends Error {}

const TASK_LABELS = { translate: "Translation", summary: "Summary", anatomy: "Anatomy",
  chat: "Chat", explain: "Explain", equation: "Equations", brief: "Glossary" };

function showRateLimit() {
  $("ai-banner").textContent = "구독 사용 한도에 도달했습니다. 남은 번역은 원문으로 표시합니다. 잠시 후 다시 번역하세요.";
  show($("ai-banner"));
}

async function loadConfig() {
  const res = await fetch("/api/config", { cache: "no-store" });
  S.aiConfig = res.ok ? await res.json() : null;
  const backend = S.aiConfig && S.aiConfig.backend;
  const row = backend && S.aiConfig.backends.find((b) => b.id === backend);
  $("ai-badge").textContent = "AI: " + (row ? row.label : "not set");
  if (!backend) openSettings();
}

function fillModels(backend) {
  const box = $("set-models");
  box.innerHTML = "";
  const row = S.aiConfig.backends.find((b) => b.id === backend);
  const listId = "suggest-" + backend;
  const list = document.createElement("datalist"); list.id = listId;
  for (const m of row.suggestions) { const o = document.createElement("option"); o.value = m; list.appendChild(o); }
  box.appendChild(list);
  for (const task of S.aiConfig.tasks) {
    const label = document.createElement("label");
    label.textContent = TASK_LABELS[task] || task;
    const input = document.createElement("input");
    input.dataset.task = task; input.setAttribute("list", listId);
    input.value = S.aiConfig.models[backend][task];
    label.appendChild(input); box.appendChild(label);
  }
  $("set-conc").value = S.aiConfig.concurrency[backend];
  $("set-provider").textContent = row.provider ? "Provider: " + row.provider : "";
}

function openSettings() {
  if (!S.aiConfig) return;
  const sel = $("set-backend");
  sel.innerHTML = "";
  for (const b of S.aiConfig.backends) {
    const o = document.createElement("option");
    o.value = b.id; o.textContent = b.label + (b.installed ? "" : " (not installed)");
    o.disabled = !b.installed; sel.appendChild(o);
  }
  const first = S.aiConfig.backend || (S.aiConfig.backends.find((b) => b.installed) || {}).id;
  if (first) { sel.value = first; fillModels(first); }
  $("settings-reason").textContent = S.aiConfig.reason || "";
  (S.aiConfig.reason ? show : hide)($("settings-reason"));
  hide($("settings-error"));
  $("settings").showModal();
}

$("ai-badge").addEventListener("click", openSettings);
$("set-backend").addEventListener("change", (e) => fillModels(e.target.value));
$("settings-cancel").addEventListener("click", () => $("settings").close());
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const models = {};
  for (const input of $("set-models").querySelectorAll("input[data-task]")) models[input.dataset.task] = input.value.trim();
  $("settings-save").disabled = true; $("settings-save").textContent = "Testing…";
  try {
    await postJSON("/api/settings", { backend: $("set-backend").value, models, concurrency: parseInt($("set-conc").value, 10) });
    location.reload();  // every cache key and the pool size follow the new settings
  } catch (err) {
    $("settings-error").textContent = err.message; show($("settings-error"));
  } finally {
    $("settings-save").disabled = false; $("settings-save").textContent = "Save";
  }
});
```

3. Replace `const TR_POOL = 8;` with a function and use it in `translateAll`:

```javascript
const trPool = () => (S.aiConfig && S.aiConfig.backend ? S.aiConfig.concurrency[S.aiConfig.backend] : 1);
```

In `translateAll`, change `Array.from({ length: TR_POOL }, worker)` to `Array.from({ length: trPool() }, worker)`, add `S.rateLimited = null; hide($("ai-banner"));` right after `const token = ++S.trToken;`, add `if (S.rateLimited === token) return;` as the first line inside the worker loop, and change the math second pass loop guard to `if (token !== S.trToken || S.rateLimited === token) return;`. At the top of `translateAll` add `if (!S.aiConfig || !S.aiConfig.backend) return;`.

4. In `streamTranslate`, remove `model: model(),` from the body and make error lines throw:

```javascript
      let obj; try { obj = JSON.parse(line); } catch { continue; }
      if (obj.error) throw obj.rate_limited ? new RateLimited(obj.error) : new Error(obj.error);
      write(offset + obj.i, obj.tr);
```

Also treat an HTTP 429 response as a rate limit: replace the `if (!res.ok) throw …` line with:

```javascript
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body.rate_limited ? new RateLimited(body.detail) : new Error(body.detail || res.statusText);
  }
```

5. In `translateBatch`, change the retry loop body so a rate limit stops all retries:

```javascript
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!live() || S.rateLimited === token) break;
    try {
      await streamTranslate(page, b.start, b.texts, force || attempt > 0, write);
    } catch (err) {
      if (err instanceof RateLimited) { S.rateLimited = token; showRateLimit(); break; }
    }
    if (!hasGap()) break;
    if (attempt < 2) await sleep(400);
  }
```

(The existing "fill any segment still missing with the source text" block after the loop stays.)

6. `fetchMath`: change the URL to `` `/api/math/${S.docId}/${i}` ``.

7. Remove `model: model()` from the `/api/anatomy`, `/api/summarize`, `/api/chat`, and `/api/explain` bodies. In `loadAnatomy`, change `if (!force && S.anatomyLoaded && S.anatomyModel === model()) return;` to `if (!force && S.anatomyLoaded) return;` and delete `S.anatomyModel = model();`. Delete the `$("model").addEventListener("change", …)` block.

8. In `postJSON`, surface rate limits:

```javascript
async function postJSON(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    if (data.rate_limited) showRateLimit();
    throw new Error(data.detail || res.statusText);
  }
  return res.json();
}
```

9. Chat history: add

```javascript
async function loadChat() {
  $("chatlog").innerHTML = "";
  try {
    const r = await (await fetch(`/api/chat/${S.docId}`)).json();
    for (const t of r.turns) {
      const div = addMsg(t.role === "user" ? "user" : "bot", t.text);
      if (t.role !== "user") { div.innerHTML = marked.parse(t.text); renderMath(div); }
    }
  } catch { /* empty chat on failure */ }
}
$("chat-reset").addEventListener("click", async () => {
  if (!S.docId) return;
  await postJSON(`/api/chat/${S.docId}/reset`, {});
  $("chatlog").innerHTML = "";
});
```

and call `loadChat();` in `openDoc` right after `genSummary();`.

10. Startup: change the final `loadLibrary();` call at the bottom of the file to `loadConfig().finally(loadLibrary);`. In `openDoc`, guard the AI kick-offs so a missing backend never sends requests: wrap `genSummary(); loadAnatomy(); loadChat();` and the `if (autotr) translateAll();` line with `if (S.aiConfig && S.aiConfig.backend) { … }`, and compute `const autotr = $("autotr").checked && !!(S.aiConfig && S.aiConfig.backend);` so the loading cover does not wait for a first sentence that never comes.

- [ ] **Step 6: Run checks**

Run: `uv run python -m unittest tests.test_frontend -v && node --check web/app.js`
Expected: PASS.

- [ ] **Step 7: Manual check in the browser**

Run: `uv run uvicorn app.main:app --port 8000`, open `http://localhost:8000`.
Expected:
- The badge reads "AI: Hermes" or "AI: Claude Code"; clicking it opens the dialog with one model field per Task.
- Saving `gpt-6.1-luna` for Translation on Hermes shows the "not available for your account" error and keeps the dialog open.
- Opening a paper translates with the configured pool; the chat tab shows the earlier turns after a reload; "대화 지우기" clears them.

- [ ] **Step 8: Commit**

```bash
git add web/index.html web/app.js web/styles.css tests/frontend_ratelimit.test.js tests/test_frontend.py
git commit -m "Add AI settings dialog, per-backend pool size, rate-limit stop, saved chat"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`, `INSTALL.md`

**Interfaces:**
- Consumes: the behavior shipped in Tasks 1–7.
- Produces: user and agent docs; the "Adding an AI Backend" checklist that OpenClaw will follow.

- [ ] **Step 1: README.md**

Replace every statement that the app "uses the Claude Code CLI" with: Paper Reader sends AI requests through the AI Backend chosen in Settings, Claude Code or Hermes, using that tool's own subscription login. Add a short "AI settings" section:

```markdown
## AI settings

Click the **AI** badge in the top bar to choose the AI Backend (Claude Code or Hermes), a model
for each task, and how many translation requests run at once (default 8). Saving sends one short
test request per model and refuses models your account cannot use. Settings are stored in
`data/settings.json`. `PAPER_READER_BACKEND=claude|hermes` overrides the saved choice for one run.

On first launch, if only one of the tools is installed, Paper Reader picks it after a successful
test request. If both are installed, the settings dialog opens. If neither is, PDFs still open and
AI features stay off.
```

- [ ] **Step 2: AGENTS.md**

Add:

```markdown
## AI Backends

- `app/ai.py` builds every prompt and picks the Model for each Task from `app/settings.py`.
  Nothing outside `app/backends/` starts an AI process.
- Requests that carry PDF text or images run with every agent tool disabled (ADR 0002).
  Equation images go inline in the message; never enable a file-read tool for them.
- Never read or pass an API key. The Claude Code env drops `ANTHROPIC_API_KEY`; Hermes keeps the
  user's Provider untouched.
- Errors shown to users come from `app/errors.py`; never put stderr or provider bodies in them.

### Adding an AI Backend (OpenClaw is next)

1. Add `app/backends/<name>.py` exposing `NAME`, `LABEL`, `MODEL_SUGGESTIONS`, `installed()`,
   `describe()`, `async run(prompt, *, model, system=None, images=())`, and
   `stream(prompt, *, model, system=None)`.
2. Disable every tool in that transport and send images inline.
3. Map its errors with `app.errors.from_text` so rate limits become `RateLimitError`.
4. Register it in `app/backends/__init__.py` and add its name and default Models to
   `app/settings.py` (`BACKEND_NAMES`, `DEFAULT_MODELS`).
5. Add `tests/test_backend_<name>.py` mirroring `tests/test_backend_hermes.py`.
```

Remove any AGENTS.md statement that the app is Claude-only or that `app/claude.py` is the only seam.

- [ ] **Step 3: INSTALL.md**

Under requirements, change "Claude Code CLI, logged in" to "Claude Code or Hermes, installed and logged in (`claude` or `hermes setup`)". Add to troubleshooting: "AI badge says *not set*: open it and pick a backend; the dialog says what is missing." and "Hermes models: ChatGPT-account Codex supports fewer models than the Hermes catalog lists; the settings test request tells you which."

- [ ] **Step 4: Run all checks**

Run: `uv run python -m unittest discover -s tests && uvx ruff format --check . && uvx ruff check . && node --check web/app.js`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md INSTALL.md GLOSSARY.md docs/adr
git commit -m "Document AI Backends, settings, and adding a backend"
```

---

## Next plan (not part of this one)

Paper ID (DOI, arXiv ID, Zotero item key, File Fingerprint), Bibliographic Record lookup from CrossRef/arXiv, and re-keying Chat History from `doc_id` to Paper ID per ADR 0001. Zotero integration follows that.
