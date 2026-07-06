from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import VAULT_DIR

WORMHOLE_FILENAME = ".wormhole.md"
WORMHOLE_ROOT_MARKER = ".wormhole-root"
OPEN_MARKER = "<!-- WORMHOLE:OPEN"
CLOSE_MARKER = "<!-- WORMHOLE:CLOSED"
END_MARKER = "<!-- WORMHOLE:END -->"


class PaneDetectionError(RuntimeError):
    pass


def current_pane_tty() -> str:
    """Return the controlling TTY of the pane that hosts this process.

    `wh` is typically invoked from inside an AI CLI via its shell escape
    (e.g. `! wh fold claude`). In that path the CLI may attach pipes to
    the subprocess, so this process's own stdio is not a TTY. Walk the
    parent-process chain until a process with a controlling TTY is found.
    """
    for fd in (0, 1, 2):
        try:
            return os.ttyname(fd)
        except OSError:
            continue
    pid = os.getppid()
    while pid > 1:
        tty = _tty_of(pid)
        if tty:
            return tty
        pid = _ppid_of(pid)
    raise PaneDetectionError(
        "no pane TTY found — wh must run inside a terminal pane"
    )


def pane_key(path: str | os.PathLike) -> str:
    """Stable key derived from an absolute path. All CLIs whose project
    root resolves to the same path share the same wormhole."""
    return hashlib.sha1(str(path).encode()).hexdigest()[:8]


def project_root(cwd: Path) -> Path:
    """Wormhole sharing root walking up from cwd.

    `.wormhole-root` wins over nested git repos. This lets a studio/monorepo
    choose one global context file even when child projects have their own
    `.git` directories. Without the marker, use the nearest git toplevel and
    fall back to cwd.
    """
    cwd = cwd.resolve()
    for d in [cwd, *cwd.parents]:
        if (d / WORMHOLE_ROOT_MARKER).exists():
            return d
    for d in [cwd, *cwd.parents]:
        if (d / ".git").exists():
            return d
    return cwd


@dataclass
class PanePaths:
    cwd: Path
    tty: str
    key: str
    vault: Path
    meta_json: Path
    wormhole_md: Path        # the real file in the vault
    cwd_link: Path           # <cwd>/.wormhole.md, symlink to wormhole_md

    @classmethod
    def for_current(cls, cwd: Path | None = None) -> "PanePaths":
        cwd = (cwd or Path.cwd()).resolve()
        override = os.environ.get("WORMHOLE_PANE_KEY")
        root = project_root(cwd)
        key = override or pane_key(root)
        try:
            tty = current_pane_tty()
        except PaneDetectionError:
            tty = "unknown"
        vault = VAULT_DIR / "panes" / key
        return cls(
            cwd=cwd,
            tty=tty,
            key=key,
            vault=vault,
            meta_json=vault / "meta.json",
            wormhole_md=vault / "wormhole.md",
            cwd_link=root / WORMHOLE_FILENAME,
        )

    def ensure(self) -> None:
        self.vault.mkdir(parents=True, exist_ok=True)
        if not self.meta_json.exists():
            try:
                ctime = os.stat(self.tty).st_ctime_ns
            except OSError:
                ctime = 0
            self.meta_json.write_text(json.dumps({
                "tty": self.tty,
                "ctime": ctime,
                "term_program": os.environ.get("TERM_PROGRAM"),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sources": [],
            }, indent=2))

    def record_source(self, source: str) -> None:
        meta = json.loads(self.meta_json.read_text())
        sources = meta.get("sources", [])
        if source not in sources:
            sources.append(source)
            meta["sources"] = sources
        meta["last_fold_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.meta_json.write_text(json.dumps(meta, indent=2))

    def streaming_sources(self) -> list[str]:
        if not self.meta_json.exists():
            return []
        try:
            meta = json.loads(self.meta_json.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return list(meta.get("streaming") or [])

    def mark_streaming(self, source: str) -> None:
        meta = json.loads(self.meta_json.read_text())
        streaming = list(meta.get("streaming") or [])
        if source not in streaming:
            streaming.append(source)
        meta["streaming"] = streaming
        self.meta_json.write_text(json.dumps(meta, indent=2))

    def unmark_streaming(self, source: str) -> None:
        if not self.meta_json.exists():
            return
        try:
            meta = json.loads(self.meta_json.read_text())
        except (OSError, json.JSONDecodeError):
            return
        streaming = [s for s in (meta.get("streaming") or []) if s != source]
        meta["streaming"] = streaming
        self.meta_json.write_text(json.dumps(meta, indent=2))

    def is_open(self) -> bool:
        if not self.wormhole_md.exists():
            return False
        try:
            with self.wormhole_md.open() as f:
                first = f.readline()
        except OSError:
            return False
        return first.startswith(OPEN_MARKER)

    @contextmanager
    def lock(self):
        """Serialize concurrent folds within a pane (cross-CLI safety)."""
        self.vault.mkdir(parents=True, exist_ok=True)
        lock_path = self.vault / ".lock"
        with lock_path.open("w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def link_into_cwd(self) -> None:
        """Ensure <cwd>/.wormhole.md points at this pane's wormhole.md."""
        target = self.wormhole_md
        link = self.cwd_link
        if link.is_symlink() or link.exists():
            try:
                if link.is_symlink() and Path(os.readlink(link)) == target:
                    return
            except OSError:
                pass
            link.unlink()
        link.symlink_to(target)


def any_pane_streams(source: str) -> bool:
    panes_dir = VAULT_DIR / "panes"
    if not panes_dir.is_dir():
        return False
    for meta_path in panes_dir.glob("*/meta.json"):
        try:
            data = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if source in (data.get("streaming") or []):
            return True
    return False


def parent_chain() -> list[str]:
    """Process names of all ancestors up to PID 1. Cheapest-first list.
    Used by adapters to detect whether they are the active host CLI."""
    chain: list[str] = []
    pid = os.getppid()
    while pid > 1:
        comm = _ps(pid, "comm=")
        if comm:
            chain.append(comm.strip())
        next_pid = _ppid_of(pid)
        if next_pid == pid or next_pid <= 0:
            break
        pid = next_pid
    return chain


def _tty_of(pid: int) -> str | None:
    out = _ps(pid, "tty=")
    if not out or out in ("?", "??"):
        return None
    return out if out.startswith("/") else f"/dev/{out}"


def _ppid_of(pid: int) -> int:
    out = _ps(pid, "ppid=")
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def _ps(pid: int, fmt: str) -> str | None:
    try:
        return subprocess.check_output(
            ["ps", "-p", str(pid), "-o", fmt],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def render_wormhole_md(
    *,
    pane_key: str,
    tty: str,
    sources_blocks: list[str],
    ts: datetime | None = None,
    state: str = "OPEN",
) -> str:
    ts = ts or datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M")
    header = f"<!-- WORMHOLE:{state} pane={pane_key} tty={tty} ts={ts_str} -->"
    body = "\n\n".join(b.rstrip() for b in sources_blocks if b.strip())
    return f"{header}\n\n{body}\n{END_MARKER}\n"


def render_block(*, source: str, session_id: str, includes: list[str], body: str, ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    ts_str = ts.strftime("%H:%M")
    inc = ",".join(includes) if includes else "qa"
    return f"[{source} session={session_id} @ {ts_str} includes={inc}]\n{body.rstrip()}\n"


def parse_block_header(block: str) -> tuple[str, str] | None:
    """Return (source, session_id) parsed from a block's header line, or None."""
    first = block.lstrip().splitlines()[0] if block.strip() else ""
    if not (first.startswith("[") and "]" in first):
        return None
    inside = first[1:first.index("]")]
    parts = inside.split()
    if not parts:
        return None
    source = parts[0]
    session_id = ""
    for p in parts[1:]:
        if p.startswith("session="):
            session_id = p[len("session="):]
            break
    return source, session_id


def split_blocks(text: str) -> tuple[str | None, list[str], str | None]:
    """Parse an existing wormhole.md into (header, source_blocks, footer).
    Returns (None, [], None) if markers are missing.
    """
    if OPEN_MARKER not in text and CLOSE_MARKER not in text:
        return None, [], None
    lines = text.splitlines()
    header_line = next((l for l in lines if l.startswith(OPEN_MARKER) or l.startswith(CLOSE_MARKER)), None)
    if header_line is None or END_MARKER not in text:
        return None, [], None
    inner_start = lines.index(header_line) + 1
    end_idx = next(i for i, l in enumerate(lines) if l.startswith(END_MARKER))
    inner = "\n".join(lines[inner_start:end_idx]).strip()
    blocks: list[str] = []
    current: list[str] = []
    for line in inner.splitlines():
        if line.startswith("[") and "@" in line and "]" in line and current:
            blocks.append("\n".join(current).rstrip())
            current = [line]
        else:
            current.append(line)
    if current:
        joined = "\n".join(current).rstrip()
        if joined:
            blocks.append(joined)
    return header_line, blocks, END_MARKER


def flip_to_closed(text: str) -> str:
    return text.replace(OPEN_MARKER, CLOSE_MARKER, 1)
