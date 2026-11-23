"""Convenience access to a process-wide default Engine.

Frontends should build their own Engine from a resolved Config; this module is for
simple one-off use (the CLI, the demo, quick scripts).
"""
from __future__ import annotations

from .engine import Engine

_engine: Engine | None = None


def default_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def analyze_text(text: str, check: str | None = None, sensitivity: str | None = None) -> dict:
    return default_engine().analyze(text, check, sensitivity)
