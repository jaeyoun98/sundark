"""AI Backends. Each module exposes NAME, LABEL, installed(), describe(), run(), stream().
Register a new AI Backend (e.g. OpenClaw) by importing it here and adding it to BACKENDS."""

from __future__ import annotations

BACKENDS: dict = {}


def get(name: str):
    return BACKENDS[name]
