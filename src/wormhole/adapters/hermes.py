from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn


def _hermes_home() -> Path:
    # Hermes resolves $HERMES_HOME at startup and falls back to ~/.hermes.
    # Honor the env var so wormhole works under `hermes --profile <name>`,
    # which sets HERMES_HOME to a profile dir.
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def _state_db() -> Path:
    return _hermes_home() / "state.db"


class HermesAdapter(Adapter):
    name = "hermes"

    @classmethod
    def detect(cls, parent_chain, env):
        if env.get("HERMES_HOME") or env.get("HERMES_SESSION_ID"):
            return True
        return any(c == "hermes" or c.startswith("hermes-") for c in parent_chain)

    def find_session(self, cwd: Path) -> SessionRef:
        db = _state_db()
        if not db.exists():
            raise AdapterError(f"no Hermes state.db at {db}")
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                # Hermes does not store cwd on the session row, so we pick the
                # most recent cli session and rely on the same one-CLI-per-pane
                # contract that kiro adapter uses. Sessions with at least one
                # logged message win over freshly-opened ones with none — the
                # initial `! wh fold` itself is a shell escape that hermes
                # doesn't log, so the current session may be empty until a
                # real turn happens.
                row = conn.execute(
                    """
                    SELECT s.id
                    FROM sessions s
                    WHERE s.source = 'cli'
                      AND EXISTS (
                        SELECT 1 FROM messages m WHERE m.session_id = s.id
                      )
                    ORDER BY s.started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error as e:
            raise AdapterError(f"hermes state.db read failed: {e}") from e
        if not row:
            raise AdapterError(f"no Hermes cli session with messages in {db}")
        return SessionRef(id=row[0], cli=self.name, path=db)

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        db = session.path or _state_db()
        if not db.exists():
            raise AdapterError(f"no Hermes state.db at {db}")
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                rows = list(conn.execute(
                    """
                    SELECT role, content, tool_calls, reasoning
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id
                    """,
                    (session.id,),
                ))
        except sqlite3.Error as e:
            raise AdapterError(f"hermes state.db read failed: {e}") from e

        turns: list[Turn] = []
        for role, content, tool_calls_json, reasoning in rows:
            if role == "user":
                text = _strip_model_switch_note(content or "")
                if text:
                    turns.append(Turn(role="user", text=text))
            elif role == "assistant":
                text = (content or "").strip()
                thinking = (reasoning or "").strip()
                tool_calls = _format_tool_calls(tool_calls_json)
                if text or thinking or tool_calls:
                    turns.append(Turn(
                        role="assistant",
                        text=text,
                        thinking=thinking,
                        tool_calls=tool_calls,
                    ))
            # role == "tool" — replay payload from a tool result; skip, the
            # assistant tool_calls entry already captured what was called.
        return turns


def _strip_model_switch_note(text: str) -> str:
    # Hermes prepends `[Note: model was just switched ...]` to user turns
    # right after a model swap. It's noise for downstream readers.
    s = text.lstrip()
    if s.startswith("[Note: model was just switched"):
        end = s.find("]")
        if end != -1:
            s = s[end + 1:].lstrip()
    return s.strip()


def _format_tool_calls(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        calls = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(calls, list):
        return []
    out: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or "tool"
        args = fn.get("arguments")
        if isinstance(args, str):
            # Hermes stores OpenAI-style stringified JSON args.
            try:
                args_obj = json.loads(args)
                args_str = json.dumps(args_obj, ensure_ascii=False)
            except json.JSONDecodeError:
                args_str = args
        else:
            try:
                args_str = json.dumps(args or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = str(args)
        if len(args_str) > 400:
            args_str = args_str[:397] + "..."
        out.append(f"{name}({args_str})")
    return out
