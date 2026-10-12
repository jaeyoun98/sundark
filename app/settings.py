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
TASKS = (
    "translate",
    "summary",
    "anatomy",
    "chat",
    "explain",
    "equation",
    "brief",
    "review",
)
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


def validate(backend: str, models: dict[str, str], concurrency: int) -> None:
    """Raise ValueError for anything save() would refuse; cheap, no AI calls."""
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


def save(backend: str, models: dict[str, str], concurrency: int) -> None:
    validate(backend, models, concurrency)
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
