from __future__ import annotations

import json
from pathlib import Path

from wormhole.adapters.base import FoldOptions
from wormhole.adapters.claude import ClaudeAdapter, _extract_user_text, _extract_assistant_parts
from wormhole.adapters.gemini import GeminiAdapter
from wormhole.pane import (
    flip_to_closed,
    pane_key,
    parse_block_header,
    project_root,
    render_block,
    render_wormhole_md,
    split_blocks,
)


def test_pane_key_is_stable():
    # Same input → same key. Real ctime is read for /dev/null which exists everywhere.
    a = pane_key("/dev/null")
    b = pane_key("/dev/null")
    assert a == b
    assert len(a) == 8


def test_pane_key_distinguishes_paths():
    # Different paths → different keys (effectively always; collisions are 1/2^32).
    assert pane_key("/dev/null") != pane_key("/dev/zero")


def test_project_root_walks_to_git(tmp_path: Path):
    repo = tmp_path / "repo"
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    assert project_root(sub) == repo.resolve()
    assert project_root(repo) == repo.resolve()


def test_project_root_falls_back_to_cwd(tmp_path: Path):
    plain = tmp_path / "no-git"
    plain.mkdir()
    assert project_root(plain) == plain.resolve()


def test_pane_key_matches_for_subdirs_of_same_project(tmp_path: Path):
    repo = tmp_path / "proj"
    (repo / "a").mkdir(parents=True)
    (repo / "b" / "c").mkdir(parents=True)
    (repo / ".git").mkdir()
    k1 = pane_key(project_root(repo / "a"))
    k2 = pane_key(project_root(repo / "b" / "c"))
    assert k1 == k2


def test_extract_user_text_string():
    assert _extract_user_text({"role": "user", "content": "hi"}) == "hi"


def test_extract_user_text_skips_tool_result():
    msg = {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}
    assert _extract_user_text(msg) is None


def test_extract_assistant_parts_filters_thinking():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "answer"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        ],
    }
    text, thinking, tools = _extract_assistant_parts(msg)
    assert text == "answer"
    assert thinking == "hmm"
    assert tools == ['Bash({"command": "ls"})']


def test_render_wormhole_md_open():
    block = render_block(source="claude", session_id="abc12345", includes=["qa"], body="**user:** hi\n**claude:** hey\n")
    out = render_wormhole_md(pane_key="abc12345", tty="/dev/pts/4", sources_blocks=[block])
    assert "<!-- WORMHOLE:OPEN" in out
    assert "<!-- WORMHOLE:END -->" in out
    assert "pane=abc12345" in out
    assert "tty=/dev/pts/4" in out
    assert "[claude session=abc12345" in out


def test_flip_to_closed():
    block = render_block(source="claude", session_id="x", includes=["qa"], body="hi\n")
    out = render_wormhole_md(pane_key="x", tty="/dev/pts/0", sources_blocks=[block])
    flipped = flip_to_closed(out)
    assert "<!-- WORMHOLE:CLOSED" in flipped
    assert "<!-- WORMHOLE:OPEN" not in flipped


def test_parse_block_header():
    block = render_block(source="claude", session_id="abc12345", includes=["qa"], body="x\n")
    assert parse_block_header(block) == ("claude", "abc12345")
    assert parse_block_header("not a block") is None


def test_split_blocks_round_trip():
    blocks = [
        render_block(source="claude", session_id="s1", includes=["qa"], body="**user:** a\n**claude:** b\n"),
        render_block(source="claude", session_id="s2", includes=["qa"], body="**user:** c\n**claude:** d\n"),
        render_block(source="gemini", session_id="g1", includes=["qa"], body="**user:** e\n**gemini:** f\n"),
    ]
    md = render_wormhole_md(pane_key="k", tty="/dev/pts/1", sources_blocks=blocks)
    header, parsed, end = split_blocks(md)
    assert header is not None
    assert end is not None
    assert len(parsed) == 3
    headers = [parse_block_header(b) for b in parsed]
    assert headers == [("claude", "s1"), ("claude", "s2"), ("gemini", "g1")]


def test_split_blocks_missing_markers():
    header, blocks, end = split_blocks("just some text")
    assert header is None
    assert blocks == []
    assert end is None


def test_hook_install_remove_idempotent(tmp_path: Path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": []}}))
    from wormhole import hook
    monkeypatch.setattr(hook, "CLAUDE_SETTINGS", settings)

    assert hook.install_claude() is True
    assert hook.install_claude() is False  # idempotent
    assert hook.claude_installed() is True

    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": []}  # untouched
    assert any(
        h.get("command") == hook.HOOK_COMMAND
        for m in data["hooks"]["Stop"]
        for h in m.get("hooks", [])
    )

    assert hook.remove_claude() is True
    assert hook.remove_claude() is False  # idempotent
    assert hook.claude_installed() is False
    assert "hooks" not in json.loads(settings.read_text())


def test_gemini_hook_install_remove_idempotent(tmp_path: Path):
    settings = tmp_path / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"general": {"vimMode": True}}))
    from wormhole import hook

    assert hook.install_gemini(tmp_path) is True
    assert hook.install_gemini(tmp_path) is False  # idempotent
    assert hook.gemini_installed(tmp_path) is True

    data = json.loads(settings.read_text())
    assert data["general"]["vimMode"] is True  # untouched
    assert any(
        h.get("command") == hook.HOOK_COMMAND
        for m in data["hooks"]["AfterAgent"]
        for h in m.get("hooks", [])
    )

    assert hook.remove_gemini(tmp_path) is True
    assert hook.remove_gemini(tmp_path) is False  # idempotent
    assert hook.gemini_installed(tmp_path) is False
    assert "hooks" not in json.loads(settings.read_text())


def test_opencode_hook_install_remove_idempotent(tmp_path: Path):
    settings = tmp_path / "opencode.json"
    settings.write_text(json.dumps({"instructions": ["A.md"]}))
    from wormhole import hook

    assert hook.install_opencode(tmp_path) is True
    assert hook.install_opencode(tmp_path) is False  # idempotent
    assert hook.opencode_installed(tmp_path) is True

    # Check plugin file exists
    plugin_path = tmp_path / ".wormhole" / "opencode.ts"
    assert plugin_path.exists()
    assert hook.HOOK_COMMAND in plugin_path.read_text()

    # Check opencode.json updated
    data = json.loads(settings.read_text())
    assert data["instructions"] == ["A.md"]
    assert "./.wormhole/opencode.ts" in data["plugin"]

    assert hook.remove_opencode(tmp_path) is True
    assert hook.remove_opencode(tmp_path) is False  # idempotent
    assert hook.opencode_installed(tmp_path) is False
    assert not plugin_path.exists()
    assert "plugin" not in json.loads(settings.read_text())


def test_hook_remove_preserves_other_stop_hooks(tmp_path: Path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-thing"}]}]}
    }))
    from wormhole import hook
    monkeypatch.setattr(hook, "CLAUDE_SETTINGS", settings)

    hook.install_claude()
    hook.remove_claude()
    data = json.loads(settings.read_text())
    flat = [h for m in data["hooks"]["Stop"] for h in m.get("hooks", [])]
    assert any(h["command"] == "other-thing" for h in flat)
    assert not any(h["command"] == hook.HOOK_COMMAND for h in flat)


def test_streaming_state_round_trip(tmp_path: Path):
    from wormhole.pane import PanePaths
    paths = PanePaths(
        cwd=tmp_path,
        tty="/dev/pts/9",
        key="testkey1",
        vault=tmp_path / "vault",
        meta_json=tmp_path / "vault" / "meta.json",
        wormhole_md=tmp_path / "vault" / "wormhole.md",
        cwd_link=tmp_path / ".wormhole.md",
    )
    paths.ensure()
    assert paths.streaming_sources() == []
    paths.mark_streaming("claude")
    assert paths.streaming_sources() == ["claude"]
    paths.mark_streaming("claude")  # idempotent
    assert paths.streaming_sources() == ["claude"]
    paths.unmark_streaming("claude")
    assert paths.streaming_sources() == []


def test_claude_adapter_returns_all_turns(tmp_path: Path):
    sf = tmp_path / "deadbeef.jsonl"
    events = [
        {"type": "user", "message": {"role": "user", "content": "first"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}},
        # tool_result replay should be ignored
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "message": {"role": "user", "content": "second"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "world"}]}},
    ]
    with sf.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    from wormhole.adapters.base import SessionRef

    adapter = ClaudeAdapter()
    ref = SessionRef(id="deadbeef", cli="claude", path=sf)
    turns = adapter.read_turns(ref, FoldOptions())
    roles = [t.role for t in turns]
    texts = [t.text for t in turns]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert texts == ["first", "hello", "second", "world"]


def test_gemini_adapter_returns_all_turns(tmp_path: Path):
    sf = tmp_path / "gemini.jsonl"
    events = [
        {"type": "user", "id": "1", "content": "hello gemini"},
        {"type": "gemini", "id": "2", "content": "hi there", "thoughts": [{"subject": "t1", "text": "thinking"}]},
        {"type": "user", "id": "3", "content": "next"},
        {"type": "gemini", "id": "4", "content": "bye", "toolCalls": [{"name": "ls", "args": {}}]},
    ]
    with sf.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    from wormhole.adapters.base import SessionRef

    adapter = GeminiAdapter()
    ref = SessionRef(id="test", cli="gemini", path=sf)
    turns = adapter.read_turns(ref, FoldOptions(include_thinking=True, include_tools=True))

    assert len(turns) == 4
    assert turns[0].role == "user"
    assert turns[0].text == "hello gemini"
    assert turns[1].role == "assistant"
    assert turns[1].text == "hi there"
    assert turns[1].thinking == "t1: thinking"
    assert turns[2].role == "user"
    assert turns[2].text == "next"
    assert turns[3].role == "assistant"
    assert turns[3].text == "bye"
    assert turns[3].tool_calls == ['ls({})']
