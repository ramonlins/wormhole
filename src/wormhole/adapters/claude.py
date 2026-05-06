from __future__ import annotations

import json
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def _project_dir(cwd: Path) -> Path:
    # Claude Code encodes cwd as the absolute path with '/' replaced by '-'.
    encoded = str(cwd.resolve()).replace("/", "-")
    return CLAUDE_PROJECTS / encoded


class ClaudeAdapter(Adapter):
    name = "claude"

    @classmethod
    def detect(cls, parent_chain, env):
        if env.get("CLAUDECODE") == "1" or env.get("CLAUDE_CODE_ENTRYPOINT"):
            return True
        return any(c == "claude" or c.startswith("claude-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        proj = _project_dir(cwd)
        if not proj.is_dir():
            raise AdapterError(f"no Claude project dir for {cwd} (expected {proj})")
        candidates = sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise AdapterError(f"no Claude session jsonl in {proj}")
        latest = candidates[0]
        return SessionRef(id=latest.stem, cli=self.name, path=latest)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            raise AdapterError("session.path missing")
        events = _load_jsonl(session.path)
        turns: list[Turn] = []
        for ev in events:
            t = ev.get("type")
            msg = ev.get("message")
            if t == "user":
                user_text = _extract_user_text(msg)
                if user_text is None:
                    continue  # tool_result replay, skip
                turns.append(Turn(role="user", text=user_text))
            elif t == "assistant":
                text, thinking, tool_calls = _extract_assistant_parts(msg)
                if not text and not thinking and not tool_calls:
                    continue
                turns.append(Turn(role="assistant", text=text, thinking=thinking, tool_calls=tool_calls))
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


_SCAFFOLD_TAGS = (
    "<local-command-caveat",
    "<bash-input",
    "<bash-stdout",
    "<bash-stderr",
    "<command-message",
    "<command-name",
    "<command-args",
    "<system-reminder",
)


def _is_scaffold(text: str) -> bool:
    s = text.lstrip()
    return s.startswith(_SCAFFOLD_TAGS)


def _extract_user_text(msg) -> str | None:
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        if _is_scaffold(content):
            return None
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return None
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        kept = [t for t in texts if t and not _is_scaffold(t)]
        joined = "\n".join(kept)
        return joined or None
    return None


def _extract_assistant_parts(msg) -> tuple[str, str, list[str]]:
    if not isinstance(msg, dict):
        return "", "", []
    content = msg.get("content")
    if not isinstance(content, list):
        return "", "", []
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            text_parts.append(item.get("text", ""))
        elif kind == "thinking":
            thinking_parts.append(item.get("thinking", ""))
        elif kind == "tool_use":
            name = item.get("name", "tool")
            inp = item.get("input", {})
            try:
                inp_str = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_str = str(inp)
            if len(inp_str) > 400:
                inp_str = inp_str[:397] + "..."
            tool_calls.append(f"{name}({inp_str})")
    return "\n".join(text_parts).strip(), "\n".join(thinking_parts).strip(), tool_calls


