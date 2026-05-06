from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn

GEMINI_HOME = Path.home() / ".gemini"
GEMINI_TMP = GEMINI_HOME / "tmp"
GEMINI_PROJECTS = GEMINI_HOME / "projects.json"


def _project_dir(cwd: Path) -> Path:
    """Resolve Gemini's per-project tmp dir for cwd.

    Gemini stores per-project state in `~/.gemini/tmp/<key>/` where <key>
    is either the friendly name from projects.json or a sha256 of the
    absolute path (used when the friendly slot is taken).
    """
    abs_path = str(cwd.resolve())
    try:
        mapping = json.loads(GEMINI_PROJECTS.read_text()).get("projects", {})
    except (FileNotFoundError, json.JSONDecodeError):
        mapping = {}
    name = mapping.get(abs_path)
    if name:
        candidate = GEMINI_TMP / name
        if candidate.is_dir():
            return candidate
    # Fallback: sha256 of absolute path
    h = hashlib.sha256(abs_path.encode()).hexdigest()
    candidate = GEMINI_TMP / h
    if candidate.is_dir():
        return candidate
    raise AdapterError(
        f"no Gemini project dir for {cwd} "
        f"(checked friendly name '{name}' and sha256 fallback)"
    )


class GeminiAdapter(Adapter):
    name = "gemini"

    @classmethod
    def detect(cls, parent_chain, env):
        if env.get("GEMINI_CLI") or env.get("GEMINI_API_KEY") and any("gemini" in c for c in parent_chain):
            return True
        return any(c == "gemini" or c.startswith("gemini-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        chats = _project_dir(cwd) / "chats"
        if not chats.is_dir():
            raise AdapterError(f"no Gemini chats dir at {chats}")
        candidates = sorted(chats.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise AdapterError(f"no Gemini session jsonl in {chats}")
        latest = candidates[0]
        # session id is the last component of the filename: session-<ts>-<id>.jsonl
        sid = latest.stem.split("-")[-1] if "-" in latest.stem else latest.stem
        return SessionRef(id=sid, cli=self.name, path=latest)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            raise AdapterError("session.path missing")
        # Gemini emits one record per "step": intermediate tool-use steps have
        # empty `content` and a `toolCalls` array; the final assistant message
        # for a turn has filled `content`. Same `id` may appear twice as the
        # store back-fills toolCalls — keep the latest (merged) view.
        records: dict[str, dict] = {}
        order: list[str] = []
        with session.path.open() as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    ev = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t in (None, "main", "info") or "$set" in ev:
                    continue
                eid = ev.get("id")
                if not eid:
                    continue
                if eid in records:
                    records[eid].update(ev)
                else:
                    records[eid] = ev
                    order.append(eid)

        turns: list[Turn] = []
        for eid in order:
            ev = records[eid]
            t = ev.get("type")
            if t == "user":
                text = _user_text(ev.get("content"))
                if text:
                    turns.append(Turn(role="user", text=text))
            elif t == "gemini":
                content = ev.get("content")
                text = content.strip() if isinstance(content, str) else ""
                thinking = _thoughts_text(ev.get("thoughts") or [])
                tool_calls = _tool_calls(ev.get("toolCalls") or [])
                visible = bool(text) or (opts.include_thinking and bool(thinking)) or (opts.include_tools and bool(tool_calls))
                if not visible:
                    continue
                turns.append(Turn(
                    role="assistant",
                    text=text,
                    thinking=thinking,
                    tool_calls=tool_calls,
                ))
                # skip 'main' (session header), 'meta' ($set patches), 'info' (cancellations)
        return turns


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "\n".join(t for t in parts if t).strip()
    return ""


def _thoughts_text(thoughts: list) -> str:
    out = []
    for th in thoughts:
        if not isinstance(th, dict):
            continue
        subj = th.get("subject", "")
        desc = th.get("description") or th.get("text", "")
        line = f"{subj}: {desc}" if subj and desc else (subj or desc or "")
        if line:
            out.append(line)
    return "\n".join(out).strip()


def _tool_calls(calls: list) -> list[str]:
    out: list[str] = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("displayName") or "tool"
        args = c.get("args") or {}
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
        if len(args_str) > 400:
            args_str = args_str[:397] + "..."
        out.append(f"{name}({args_str})")
    return out
