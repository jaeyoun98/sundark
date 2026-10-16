"""Sundark's AI settings: which AI Backend, a Model per Task, and concurrency.
Non-secret; credentials stay with each AI Backend's own login."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .store import DATA

OUTPUT_LANGUAGES = (
    "Korean",
    "English",
    "Japanese",
    "Chinese",
    "Spanish",
    "French",
    "German",
)
DEFAULT_OUTPUT_LANGUAGE = "Korean"


def output_language(value: str | None = None) -> str:
    """Validate a request choice, or snapshot the saved output language."""
    if value is None:
        return load()["output_language"]
    if value not in OUTPUT_LANGUAGES:
        raise ValueError(f"Unknown output language: {value}")
    return value


BACKEND_NAMES = ("claude", "hermes", "chatgpt")
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
# Hermes keeps a cheap model for the high-volume tasks; Claude defaults every task to sonnet
# (sonnet follows the per-segment numbering contract far more reliably than haiku, which keeps
# streamed translations aligned with their source — see ai.translate_stream).
_FAST = ("translate", "equation", "brief")
DEFAULT_MODELS = {
    "claude": {t: "sonnet" for t in TASKS},
    "chatgpt": {t: ("gpt-6-luna" if t in _FAST else "gpt-6.1-sol") for t in TASKS},
    "hermes": {t: ("gpt-6-luna" if t in _FAST else "gpt-6.1-sol") for t in TASKS},
}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}")


def _env(name: str) -> str | None:
    """SUNDARK_<name>, or PAPER_READER_<name> from before the rename to Sundark."""
    return os.environ.get(f"SUNDARK_{name}") or os.environ.get(f"PAPER_READER_{name}")


def settings_path() -> Path:
    return Path(_env("SETTINGS") or str(DATA / "settings.json")).expanduser()


def _read() -> dict:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load() -> dict:
    raw = _read()
    backend = raw.get("backend") if raw.get("backend") in BACKEND_NAMES else None
    override = _env("BACKEND")
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
    return {
        "backend": backend,
        "models": models,
        "concurrency": concurrency,
        "validated_chatgpt_identity": raw.get("validated_chatgpt_identity")
        if isinstance(raw.get("validated_chatgpt_identity"), str)
        else None,
        "output_language": raw.get("output_language")
        if raw.get("output_language") in OUTPUT_LANGUAGES
        else DEFAULT_OUTPUT_LANGUAGE,
    }


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


def save(
    backend: str,
    models: dict[str, str],
    concurrency: int,
    output_language: str | None = None,
    *,
    validated_chatgpt_identity: str | None = None,
) -> None:
    validate(backend, models, concurrency)
    if output_language is not None and output_language not in OUTPUT_LANGUAGES:
        raise ValueError(f"Unknown output language: {output_language}")
    current = load()
    raw = {
        "output_language": output_language or current["output_language"],
        "backend": backend,
        "models": current["models"],
        "concurrency": current["concurrency"],
        "validated_chatgpt_identity": validated_chatgpt_identity
        if backend == "chatgpt"
        else current["validated_chatgpt_identity"],
    }
    raw["models"][backend] = {**current["models"][backend], **models}
    raw["concurrency"][backend] = concurrency
    # Keep only choices that differ from the defaults, so a later change to a default
    # reaches users who never picked that model themselves.
    pinned = {
        name: {t: m for t, m in chosen.items() if m != DEFAULT_MODELS[name][t]}
        for name, chosen in raw["models"].items()
    }
    raw["models"] = {name: chosen for name, chosen in pinned.items() if chosen}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(raw, tmp, indent=2)
    os.replace(tmp.name, path)
