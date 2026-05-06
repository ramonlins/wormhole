from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import VAULT_DIR

NOTES_PATH = VAULT_DIR / "notes.jsonl"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(event: dict[str, Any]) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": _ts(), **event}
    with NOTES_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_all() -> list[dict[str, Any]]:
    if not NOTES_PATH.exists():
        return []
    out = []
    with NOTES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def latest_for_pane(pane_key: str) -> dict[str, Any] | None:
    for rec in reversed(read_all()):
        if rec.get("pane") == pane_key:
            return rec
    return None
