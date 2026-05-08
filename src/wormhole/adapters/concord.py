from __future__ import annotations

import json
from pathlib import Path

from ..config import VAULT_DIR
from ..pane import pane_key, project_root
from .base import Adapter, AdapterError, FoldOptions, SessionRef, Turn


class ConcordAdapter(Adapter):
    """Concord/accord — multi-agent debate TUI.

    Concord doesn't keep a session log of its own; instead, after each
    judge synthesis, the Rust binary appends one record to a jsonl file
    inside this pane's wormhole folder. We just read the latest file.

    Each line:
      {"ts": "...", "user_prompt": "...", "judge_id": "...", "synthesis": "..."}

    Workers' raw replies are intentionally NOT persisted — the synthesis
    is the canonical concord output. That's the whole point of running
    concord in front of a wormhole channel.
    """

    name = "concord"

    @classmethod
    def detect(cls, parent_chain, env):
        # Concord's binary is named `accord` (legacy). Match either name and
        # any short-prefixed variants the user might launch via wrapper script.
        return any(
            c in ("concord", "accord") or c.startswith(("concord-", "accord-"))
            for c in parent_chain
        )

    def _sessions_dir(self, cwd: Path) -> Path:
        key = pane_key(project_root(cwd))
        return VAULT_DIR / "panes" / key / "concord-sessions"

    def find_session(self, cwd: Path) -> SessionRef:
        # NOTE: do NOT mkdir here — that leaks empty concord-sessions/
        # folders into every pane that ever runs `wh fold concord` (even
        # the silent --auto path triggered by a stop hook in another CLI).
        # The Rust publish side creates the dir lazily when it actually
        # has a synthesis to write.
        sdir = self._sessions_dir(cwd)
        candidates = (
            sorted(sdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if sdir.is_dir() else []
        )
        # Fixed session_id "concord" so every fold replaces the prior block
        # instead of stacking. Forensic detail (the per-run jsonl filename)
        # is still in `path`, just not in the block header. Without this,
        # the `pending` placeholder and each real run produce distinct
        # blocks that pile up in wormhole.md.
        if not candidates:
            return SessionRef(id="concord", cli=self.name, path=None)
        return SessionRef(id="concord", cli=self.name, path=candidates[0])

    @staticmethod
    def _extract_verdict(raw: str) -> str:
        """Pull the prose verdict out of concord's judge JSON envelope. If
        `raw` doesn't look like the envelope, return it unchanged so we
        don't lose content from non-debate paths (e.g., judge-only mode)."""
        if not raw or raw[0] != "{":
            return raw
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        if isinstance(obj, dict) and isinstance(obj.get("synthesis"), str):
            return obj["synthesis"].strip()
        return raw

    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        if session.path is None:
            # Placeholder session — channel opened, no synthesis yet.
            # Return a single non-empty turn so wh fold's `if not turns_data`
            # check passes and the channel actually opens.
            return [Turn(
                role="assistant",
                text="(concord channel opened — awaiting first /accord synthesis)",
            )]
        turns: list[Turn] = []
        with session.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                user = (rec.get("user_prompt") or "").strip()
                synth_raw = (rec.get("synthesis") or "").strip()
                judge = rec.get("judge_id") or "?"
                # The judge returns a JSON envelope: {"synthesis": "...",
                # "agree":[...], "conflict":[...], "unique":[...]}. Concord
                # writes the whole envelope as a string into `synthesis`.
                # For wormhole consumers, only the prose verdict is useful
                # context — strip the debate metadata. Fall back to raw text
                # if it isn't valid JSON (e.g., a worker-only short-circuit).
                synth = self._extract_verdict(synth_raw)
                if user:
                    turns.append(Turn(role="user", text=user))
                if synth:
                    # Tag the synthesis with which judge produced it so downstream
                    # consumers can weigh the verdict (claude:opus vs or-free etc.).
                    turns.append(Turn(role="assistant", text=f"(judge={judge})\n{synth}"))
        return turns
