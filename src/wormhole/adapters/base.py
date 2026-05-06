from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class AdapterError(Exception):
    pass


@dataclass
class FoldOptions:
    include_thinking: bool = False
    include_tools: bool = False

    def includes(self) -> list[str]:
        out = ["qa"]
        if self.include_thinking:
            out.append("thinking")
        if self.include_tools:
            out.append("tools")
        return out


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    thinking: str = ""
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class SessionRef:
    id: str
    cli: str
    path: Path | None = None  # source file/db, for diagnostics


class Adapter(ABC):
    name: str = "base"

    @classmethod
    def detect(cls, parent_chain: list[str], env: dict[str, str]) -> bool:
        """Return True if this adapter's CLI is the host invoking `wh`.

        Adapters should be conservative — only return True when they are
        confident, so auto-detection picks exactly one match. Default
        implementation is False; subclasses must override.
        """
        return False

    @abstractmethod
    def find_session(self, cwd: Path) -> SessionRef:
        """Locate the most recently active session for this cwd. Raise AdapterError if none."""

    @abstractmethod
    def read_turns(self, session: SessionRef, opts: FoldOptions) -> list[Turn]:
        """Return all user/assistant exchanges for the session, oldest-first."""

    def render(self, turns: list[Turn], opts: FoldOptions) -> str:
        lines: list[str] = []
        for t in turns:
            if t.role == "user":
                lines.append(f"**user:** {t.text.rstrip()}")
            else:
                if opts.include_thinking and t.thinking:
                    lines.append(f"**{self.name} (thinking):** {t.thinking.rstrip()}")
                if opts.include_tools and t.tool_calls:
                    for call in t.tool_calls:
                        lines.append(f"**{self.name} (tool):** {call}")
                if t.text.strip():
                    lines.append(f"**{self.name}:** {t.text.rstrip()}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
