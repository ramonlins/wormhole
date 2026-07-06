from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".codex"


def _state_db() -> Path:
    home = _codex_home()
    primary = home / "state_5.sqlite"
    if primary.exists():
        return primary
    candidates = sorted(
        home.glob("state_*.sqlite"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return primary


class CodexAdapter(Adapter):
    name = "codex"

    @classmethod
    def detect(cls, parent_chain, env):
        if env.get("CODEX_THREAD_ID"):
            return True
        return any(c == "codex" or c.startswith("codex-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        db = _state_db()
        if not db.exists():
            raise AdapterError(f"no Codex state DB at {db}")

        thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = None
                if thread_id:
                    row = conn.execute(
                        """
                        SELECT id, rollout_path
                        FROM threads
                        WHERE id = ?
                        LIMIT 1
                        """,
                        (thread_id,),
                    ).fetchone()
                if row is None:
                    row = conn.execute(
                        """
                        SELECT id, rollout_path
                        FROM threads
                        WHERE cwd = ?
                          AND source IN ('cli', 'vscode')
                          AND archived = 0
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (str(cwd.resolve()),),
                    ).fetchone()
        except sqlite3.Error as e:
            raise AdapterError(f"codex state DB read failed: {e}") from e

        if row is None:
            raise AdapterError(f"no Codex CLI session for cwd {cwd} in {db}")

        path = Path(row["rollout_path"]).expanduser()
        if not path.exists():
            raise AdapterError(f"Codex rollout file missing: {path}")
        return SessionRef(id=row["id"], cli=self.name, path=path)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            raise AdapterError("session.path missing")
        turns: list[Turn] = []
        pending_thinking: list[str] = []
        pending_tools: list[str] = []

        for ev in _load_jsonl(session.path):
            if ev.get("type") != "response_item":
                continue
            payload = ev.get("payload") or {}
            ptype = payload.get("type")

            if ptype == "reasoning":
                text = _reasoning_summary(payload)
                if text:
                    pending_thinking.append(text)
                continue

            if ptype == "function_call":
                call = _format_function_call(payload)
                if call:
                    pending_tools.append(call)
                continue

            if ptype != "message":
                continue

            role = payload.get("role")
            if role == "user":
                _flush_pending_assistant(turns, pending_thinking, pending_tools, opts)
                text = _message_text(payload.get("content"), "input_text")
                if text and not _is_codex_scaffold(text):
                    turns.append(Turn(role="user", text=text))
            elif role == "assistant":
                text = _message_text(payload.get("content"), "output_text")
                if text or pending_thinking or pending_tools:
                    turns.append(Turn(
                        role="assistant",
                        text=text,
                        thinking="\n".join(pending_thinking).strip(),
                        tool_calls=list(pending_tools),
                    ))
                pending_thinking.clear()
                pending_tools.clear()

        _flush_pending_assistant(turns, pending_thinking, pending_tools, opts)
        if not turns:
            return [Turn(
                role="assistant",
                text="(codex channel opened — awaiting first real turn)",
            )]
        return turns


def _load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        raise AdapterError(f"codex rollout read failed: {e}") from e
    return out


def _message_text(content, item_type: str) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        item.get("text", "")
        for item in content
        if isinstance(item, dict)
        and item.get("type") == item_type
        and isinstance(item.get("text"), str)
    ]
    return "\n".join(p for p in parts if p).strip()


def _reasoning_summary(payload: dict) -> str:
    summary = payload.get("summary") or []
    if not isinstance(summary, list):
        return ""
    parts = [
        item.get("text", "")
        for item in summary
        if isinstance(item, dict)
        and item.get("type") == "summary_text"
        and isinstance(item.get("text"), str)
    ]
    return "\n".join(p for p in parts if p).strip()


def _format_function_call(payload: dict) -> str:
    name = payload.get("name") or "tool"
    args = payload.get("arguments") or ""
    if isinstance(args, str):
        try:
            args_str = json.dumps(json.loads(args), ensure_ascii=False)
        except json.JSONDecodeError:
            args_str = args
    else:
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
    if len(args_str) > 400:
        args_str = args_str[:397] + "..."
    return f"{name}({args_str})"


def _flush_pending_assistant(
    turns: list[Turn],
    pending_thinking: list[str],
    pending_tools: list[str],
    opts: FoldOptions,
) -> None:
    if (opts.include_thinking and pending_thinking) or (opts.include_tools and pending_tools):
        turns.append(Turn(
            role="assistant",
            text="",
            thinking="\n".join(pending_thinking).strip(),
            tool_calls=list(pending_tools),
        ))
    pending_thinking.clear()
    pending_tools.clear()


def _is_codex_scaffold(text: str) -> bool:
    s = text.lstrip()
    return (
        s.startswith("# AGENTS.md instructions for ")
        and "<environment_context>" in s
    ) or s.startswith(("<environment_context>", "<user_shell_command>"))
