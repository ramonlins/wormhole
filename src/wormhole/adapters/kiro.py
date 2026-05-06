from __future__ import annotations

import json
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn

KIRO_SESSIONS = Path.home() / ".kiro" / "sessions" / "cli"


class KiroAdapter(Adapter):
    name = "kiro"

    @classmethod
    def detect(cls, parent_chain, env):
        return any(c == "kiro" or c.startswith("kiro-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        if not KIRO_SESSIONS.is_dir():
            raise AdapterError(f"no Kiro sessions dir ({KIRO_SESSIONS})")
        candidates = sorted(
            KIRO_SESSIONS.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise AdapterError(f"no Kiro session jsonl in {KIRO_SESSIONS}")
        latest = candidates[0]
        return SessionRef(id=latest.stem, cli=self.name, path=latest)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            raise AdapterError("session.path missing")
        turns: list[Turn] = []
        for event in _load_jsonl(session.path):
            kind = event.get("kind")
            data = event.get("data", {})
            if kind == "Prompt":
                text = _extract_text(data.get("content", []))
                if text:
                    turns.append(Turn(role="user", text=text))
            elif kind == "AssistantMessage":
                text, tool_calls = _extract_assistant(data.get("content", []))
                if text or tool_calls:
                    turns.append(Turn(role="assistant", text=text, tool_calls=tool_calls))
        return turns


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _extract_text(content: list) -> str:
    parts = [
        item["data"]
        for item in content
        if isinstance(item, dict) and item.get("kind") == "text" and item.get("data")
    ]
    return "\n".join(parts).strip()


def _extract_assistant(content: list) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    tool_calls: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "text" and item.get("data"):
            text_parts.append(item["data"])
        elif kind == "toolUse":
            d = item.get("data", {})
            name = d.get("name", "tool")
            inp = d.get("input", {})
            try:
                inp_str = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_str = str(inp)
            if len(inp_str) > 400:
                inp_str = inp_str[:397] + "..."
            tool_calls.append(f"{name}({inp_str})")
    return "\n".join(text_parts).strip(), tool_calls
