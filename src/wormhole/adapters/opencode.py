from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn


def _opencode_data_dir() -> Path:
    # Mirror opencode's Global.Path.data — XDG data dir with "opencode" subdir.
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "opencode"


def _resolve_db(data_dir: Path) -> Path:
    """Pick a DB file. Prefer plain `opencode.db`; otherwise newest `opencode-*.db`."""
    primary = data_dir / "opencode.db"
    if primary.exists():
        return primary
    candidates = sorted(
        data_dir.glob("opencode*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    raise AdapterError(f"no opencode*.db found in {data_dir}")


class OpenCodeAdapter(Adapter):
    name = "opencode"

    @classmethod
    def detect(cls, parent_chain, env):
        return any(c == "opencode" or c.startswith("opencode-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        db_path = _resolve_db(_opencode_data_dir())
        with _ro_connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT id FROM session
                WHERE directory = ? AND time_archived IS NULL
                ORDER BY time_updated DESC
                LIMIT 1
                """,
                (str(cwd.resolve()),),
            ).fetchone()
        if row is None:
            raise AdapterError(f"no opencode session for cwd {cwd} in {db_path}")
        return SessionRef(id=row[0], cli=self.name, path=db_path)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            raise AdapterError("session.path missing")
        with _ro_connect(session.path) as conn:
            messages = conn.execute(
                """
                SELECT id, data FROM message
                WHERE session_id = ?
                ORDER BY time_created ASC, id ASC
                """,
                (session.id,),
            ).fetchall()
            parts_by_msg: dict[str, list[dict]] = {}
            if messages:
                placeholders = ",".join("?" * len(messages))
                ids = [m[0] for m in messages]
                rows = conn.execute(
                    f"""
                    SELECT message_id, data FROM part
                    WHERE message_id IN ({placeholders})
                    ORDER BY id ASC
                    """,
                    ids,
                ).fetchall()
                for mid, raw in rows:
                    try:
                        parts_by_msg.setdefault(mid, []).append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        turns: list[Turn] = []
        for mid, raw_info in messages:
            try:
                info = json.loads(raw_info)
            except json.JSONDecodeError:
                continue
            role = info.get("role")
            parts = parts_by_msg.get(mid, [])
            if role == "user":
                text = _join_user_text(parts)
                if text:
                    turns.append(Turn(role="user", text=text))
            elif role == "assistant":
                text, thinking, tool_calls = _split_assistant_parts(parts)
                if text or thinking or tool_calls:
                    turns.append(
                        Turn(role="assistant", text=text, thinking=thinking, tool_calls=tool_calls)
                    )
        return turns


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    # Read-only URI; opencode uses WAL mode so we need immutable=0 (default).
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _join_user_text(parts: list[dict]) -> str:
    out: list[str] = []
    for p in parts:
        kind = p.get("type")
        if kind == "text":
            t = p.get("text") or p.get("content") or ""
            if t:
                out.append(t)
    return "\n".join(out).strip()


def _split_assistant_parts(parts: list[dict]) -> tuple[str, str, list[str]]:
    text: list[str] = []
    thinking: list[str] = []
    tools: list[str] = []
    for p in parts:
        kind = p.get("type")
        if kind == "text":
            t = p.get("text") or p.get("content") or ""
            if t:
                text.append(t)
        elif kind in ("reasoning", "thinking"):
            t = p.get("text") or p.get("reasoning") or p.get("thinking") or ""
            if t:
                thinking.append(t)
        elif kind in ("tool", "tool_use", "tool-call", "tool_call"):
            name = p.get("tool") or p.get("name") or "tool"
            inp = p.get("input") or p.get("args") or p.get("arguments") or {}
            try:
                inp_str = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_str = str(inp)
            if len(inp_str) > 400:
                inp_str = inp_str[:397] + "..."
            tools.append(f"{name}({inp_str})")
    return "\n".join(text).strip(), "\n".join(thinking).strip(), tools


