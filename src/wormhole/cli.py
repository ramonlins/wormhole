from __future__ import annotations

from pathlib import Path

import click

from . import adapters, hook
from .adapters.base import AdapterError, FoldOptions
from .config import Config
from .pane import (
    CLOSE_MARKER,
    OPEN_MARKER,
    PaneDetectionError,
    PanePaths,
    any_pane_streams,
    flip_to_closed,
    parent_chain,
    parse_block_header,
    render_block,
    render_wormhole_md,
    split_blocks,
)
from . import store

def _install_hook_for(source: str, cwd: Path) -> bool:
    if source == "claude":
        return hook.install_claude()
    if source == "codex":
        return hook.install_codex(cwd)
    if source == "gemini":
        return hook.install_gemini(cwd)
    if source == "hermes":
        return hook.install_hermes()
    if source == "kiro":
        return hook.install_kiro(cwd)
    if source == "opencode":
        return hook.install_opencode(cwd)
    return False


def _remove_hook_for(source: str, cwd: Path) -> bool:
    if source == "claude":
        return hook.remove_claude()
    if source == "codex":
        return hook.remove_codex(cwd)
    if source == "gemini":
        return hook.remove_gemini(cwd)
    if source == "hermes":
        return hook.remove_hermes()
    if source == "kiro":
        return hook.remove_kiro(cwd)
    if source == "opencode":
        return hook.remove_opencode(cwd)
    return False


@click.group()
def cli() -> None:
    """Wormhole — fold AI CLI sessions into shared per-pane context."""


@cli.command()
@click.argument("source", required=False)
@click.option("--include-thinking", is_flag=True, default=None, help="Include reasoning blocks.")
@click.option("--include-tools", is_flag=True, default=None, help="Include tool calls.")
@click.option("--auto", is_flag=True, default=False, help="Hook-driven mode: silent no-op unless this pane is streaming.")
def fold(source: str | None, include_thinking: bool | None, include_tools: bool | None, auto: bool) -> None:
    """Fold the host CLI's current session into this pane's wormhole.md.

    With no SOURCE, auto-detects the host CLI from the parent process chain.
    Manual invocation opens the gate and installs the host CLI's hook so each
    subsequent turn flows through. `--auto` is the hook-driven path.
    """
    cfg = Config.load()
    opts = FoldOptions(
        include_thinking=include_thinking if include_thinking is not None else cfg.fold.include_thinking,
        include_tools=include_tools if include_tools is not None else cfg.fold.include_tools,
    )

    try:
        paths = PanePaths.for_current()
    except PaneDetectionError as e:
        if auto:
            return
        raise click.ClickException(str(e))

    try:
        adapter = adapters.get(source) if source else adapters.detect_active(parent_chain())
    except AdapterError as e:
        if auto:
            return
        raise click.ClickException(str(e))

    if auto:
        if adapter.name not in paths.streaming_sources():
            return
        if paths.wormhole_md.exists() and not paths.is_open():
            return

    try:
        session = adapter.find_session(paths.cwd)
        turns_data = adapter.read_turns(session, opts)
    except AdapterError as e:
        if auto:
            return
        raise click.ClickException(str(e))

    if not turns_data:
        if auto:
            return
        raise click.ClickException(f"no turns found in {adapter.name} session {session.id}")

    body = adapter.render(turns_data, opts)
    new_block = render_block(
        source=adapter.name,
        session_id=session.id,
        includes=opts.includes(),
        body=body,
    )

    paths.ensure()
    hook_msg = ""
    with paths.lock():
        existing_blocks = _existing_blocks(paths.wormhole_md, source=adapter.name, session_id=session.id)
        rendered = render_wormhole_md(
            pane_key=paths.key,
            tty=paths.tty,
            sources_blocks=existing_blocks + [new_block],
        )
        paths.wormhole_md.write_text(rendered)
        paths.record_source(adapter.name)
        paths.link_into_cwd()

        if not auto and adapter.name not in paths.streaming_sources():
            paths.mark_streaming(adapter.name)
            if _install_hook_for(adapter.name, paths.cwd):
                hook_msg = " · hook installed"
                if adapter.name == "codex":
                    hook_msg += " (open /hooks, trust it, restart if needed)"

    store.append({
        "event": "fold",
        "pane": paths.key,
        "tty": paths.tty,
        "from": adapter.name,
        "session": session.id,
        "includes": opts.includes(),
        "auto": auto,
    })

    if auto:
        return
    click.echo(
        f"🌀 wormhole open — {adapter.name} session "
        f"\"{_short(session.id)}\" folded into pane {paths.key}{hook_msg}"
    )


@cli.command()
@click.argument("source", required=False)
def unfold(source: str | None) -> None:
    """Close the wormhole. With <source>, drop only that source's last block."""
    try:
        paths = PanePaths.for_current()
    except PaneDetectionError as e:
        raise click.ClickException(str(e))

    if not paths.wormhole_md.exists():
        raise click.ClickException(f"no wormhole.md for pane {paths.key}")

    text = paths.wormhole_md.read_text()
    if OPEN_MARKER not in text and CLOSE_MARKER not in text:
        raise click.ClickException("wormhole.md exists but lacks markers; refusing to touch")

    if source:
        header, blocks, _ = split_blocks(text)
        if header is None:
            raise click.ClickException("malformed wormhole.md; cannot drop source block")
        kept = _drop_last_block_for(blocks, source)
        if kept == blocks:
            raise click.ClickException(f"no {source} block to drop")
        rewritten = render_wormhole_md(
            pane_key=paths.key,
            tty=paths.tty,
            sources_blocks=kept,
            state="OPEN" if header.startswith(OPEN_MARKER) else "CLOSED",
        )
        with paths.lock():
            paths.wormhole_md.write_text(rewritten)
            paths.unmark_streaming(source)
        hook_msg = _maybe_uninstall_hook(source, paths.cwd)
        store.append({"event": "unfold", "pane": paths.key, "from": source})
        click.echo(f"🌀 dropped last {source} block from pane {paths.key}{hook_msg}")
        return

    with paths.lock():
        if OPEN_MARKER in text:
            paths.wormhole_md.write_text(flip_to_closed(text))
        was_streaming = paths.streaming_sources()
        for s in was_streaming:
            paths.unmark_streaming(s)
    hook_msgs = [_maybe_uninstall_hook(s, paths.cwd) for s in was_streaming]
    suffix = "".join(m for m in hook_msgs if m)
    store.append({"event": "unfold", "pane": paths.key})
    click.echo(f"🌀 wormhole closed — pane {paths.key}{suffix}")


@cli.command()
def status() -> None:
    """Show this pane's wormhole state."""
    try:
        paths = PanePaths.for_current()
    except PaneDetectionError as e:
        raise click.ClickException(str(e))

    click.echo(f"pane: {paths.key}")
    click.echo(f"tty:  {paths.tty}")
    if paths.wormhole_md.exists():
        first_line = paths.wormhole_md.read_text().splitlines()[0] if paths.wormhole_md.stat().st_size else ""
        click.echo(f"file: {paths.wormhole_md}")
        click.echo(f"head: {first_line}")
    else:
        click.echo("file: <none>")
    rec = store.latest_for_pane(paths.key)
    if rec:
        click.echo(
            f"last: {rec.get('event')} @ {rec.get('ts')} "
            f"({rec.get('from','-')}/{_short(rec.get('session','-'))})"
        )
    else:
        click.echo("last: <no recorded events>")


def _maybe_uninstall_hook(source: str, cwd: Path) -> str:
    if source not in {"claude", "codex", "gemini", "hermes", "kiro", "opencode"}:
        return ""
    if any_pane_streams(source):
        return ""
    if _remove_hook_for(source, cwd):
        return f" · {source} hook removed"
    return ""


def _existing_blocks(wormhole_md: Path, *, source: str, session_id: str) -> list[str]:
    """Return blocks from existing wormhole.md, dropping any prior block for
    the same (source, session_id) pair — we replace per-session on each fold,
    but blocks from *different* sessions in this pane are preserved."""
    if not wormhole_md.exists():
        return []
    text = wormhole_md.read_text()
    _, blocks, _ = split_blocks(text)
    return [b for b in blocks if parse_block_header(b) != (source, session_id)]


def _block_is_source(block: str, source: str) -> bool:
    parsed = parse_block_header(block)
    return parsed is not None and parsed[0] == source


def _drop_last_block_for(blocks: list[str], source: str) -> list[str]:
    for i in range(len(blocks) - 1, -1, -1):
        if _block_is_source(blocks[i], source):
            return blocks[:i] + blocks[i + 1:]
    return blocks


def _short(s: str | None) -> str:
    if not s:
        return "-"
    return s if len(s) <= 12 else s[:8]


if __name__ == "__main__":
    cli()
