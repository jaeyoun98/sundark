"""AI Backends. Each module exposes NAME, LABEL, MODEL_SUGGESTIONS, installed(), describe(),
run(), and stream(). Register a new AI Backend (e.g. OpenClaw) by importing it here and adding
it to BACKENDS; app/settings.py BACKEND_NAMES and DEFAULT_MODELS need the same name."""

from __future__ import annotations

from . import claude

BACKENDS = {claude.NAME: claude}


def get(name: str):
    return BACKENDS[name]
