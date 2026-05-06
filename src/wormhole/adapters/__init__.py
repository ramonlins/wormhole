from __future__ import annotations

import os

from .base import Adapter, AdapterError, FoldOptions, Turn
from .claude import ClaudeAdapter
from .gemini import GeminiAdapter
from .kiro import KiroAdapter
from .opencode import OpenCodeAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "claude": ClaudeAdapter,
    "gemini": GeminiAdapter,
    "kiro": KiroAdapter,
    "opencode": OpenCodeAdapter,
}


def get(name: str) -> Adapter:
    cls = ADAPTERS.get(name.lower())
    if cls is None:
        raise AdapterError(f"unknown source '{name}'. Known: {', '.join(ADAPTERS)}")
    return cls()


def detect_active(parent_chain: list[str]) -> Adapter:
    """Pick the adapter whose CLI is hosting this `wh` invocation.
    Errors if zero or multiple match — the user must then pass an explicit name.
    """
    env = dict(os.environ)
    matches = [cls for cls in ADAPTERS.values() if cls.detect(parent_chain, env)]
    if not matches:
        raise AdapterError(
            "could not auto-detect host CLI from parent process chain. "
            f"Pass it explicitly, e.g. `wh fold {next(iter(ADAPTERS))}`."
        )
    if len(matches) > 1:
        names = ", ".join(c.name for c in matches)
        raise AdapterError(
            f"multiple adapters claim this pane ({names}). Pass it explicitly."
        )
    return matches[0]()


__all__ = [
    "Adapter",
    "AdapterError",
    "FoldOptions",
    "Turn",
    "ADAPTERS",
    "get",
    "detect_active",
]
