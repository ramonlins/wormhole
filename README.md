# wormhole

Cross agent cli sharing session

---

Sidecar that folds AI CLI sessions into a shared markdown file so other AI
agents in the same pane can read each other's context. Multiple CLIs
(Claude, Gemini, OpenCode, Kiro, Concord) publish into the same
`wormhole.md`, tagged by source.

> **Concord** is a special source: it doesn't have its own session log.
> Concord's TUI calls into wormhole directly after each `/accord` judge
> synthesis. Use it as a planning layer in front of the other CLIs —
> `accord → ! wh fold → /accord <plan> → claude/kiro implements from the
> shared context`. See [the SKILL guide](skills/wormhole-setup/SKILL.md)
> for per-CLI setup details.

## Channel

```
! wh fold     — open the shared channel
! wh unfold   — close it
```

Any agent in the pane can open or close the channel. While open, every
assistant turn auto-publishes into the same `.wormhole.md`; other agents
read it before responding (per `AGENT.md`). The first `wh fold` from a CLI
also installs that CLI's hook so subsequent turns flow through without
manual invocation.

## Install

One-shot:

```bash
./setup.sh
```

Installs the package via `pipx` (installing it first if needed), verifies
`wh` is on PATH, detects which CLIs you have, and prints the per-CLI
first-run steps (trust folder for Gemini, reopen sessions so configs
reload, etc.).

Or manually (requires `pipx`):

```bash
pipx install --editable .
```

## Model

**One wormhole per terminal pane.** A pane is identified by its
controlling TTY. Sessions auto-bind — there is nothing to name.

`wh` is invoked from inside a CLI via its shell escape (e.g.
`! wh fold claude`). Because CLIs often run `!` commands with pipes
attached, `wh` cannot rely on its own stdin being a TTY. It detects the
pane TTY by walking the parent-process chain until it finds one attached
to a pty.

`pane_key = sha1(tty_path + tty_ctime)[:8]`
(ctime guards against pty-number reuse after a pane closes.)

## Commands

```
wh fold <cli>     [--include-thinking] [--include-tools]
wh unfold [<cli>]
wh status
```

`fold` appends `<cli>`'s current session into the pane's `wormhole.md`.

`unfold` flips the active OPEN marker to CLOSED. Content preserved.
Optional `<cli>` removes only that source's last block.

`status` prints the current `pane_key`, detected TTY, and which sources
have folded.

## Flags

```
--include-thinking     include assistant reasoning blocks
--include-tools        include tool calls
```

User passes only the content controls per fold; the session itself is
implicit.

## Layout

```
~/vault/wormhole/
├── notes.jsonl                       # event log (global)
└── panes/<pane_key>/
    ├── meta.json                     # {tty, ctime, created_at, sources: [...]}
    └── wormhole.md

<project>/.wormhole.md                # symlink → panes/<pane_key>/wormhole.md
                                      # rewritten on each fold in that cwd
~/.config/wormhole/config.toml        # user defaults
```

## `wormhole.md` format

```markdown
<!-- WORMHOLE:OPEN pane=<pane_key> tty=/dev/pts/4 ts=... -->

[claude @ 14:32 includes=qa]
**user:** ...
**claude:** ...

[gemini @ 14:45 includes=qa]
**user:** ...
**gemini:** ...
<!-- WORMHOLE:END -->
```

## Config (`~/.config/wormhole/config.toml`)

```toml
[fold]
include_thinking = false
include_tools = false
```

## Event log (`notes.jsonl`)

```json
{"event":"fold","ts":"...","pane":"<pane_key>","tty":"/dev/pts/4","from":"claude","includes":["qa"]}
{"event":"unfold","ts":"...","pane":"<pane_key>"}
```

## AGENTS.md instruction (per CLI)

```markdown
# Shared AI Context Instructions

If `.wormhole.md` exists in the working directory, read it before each
response as live context. Follow any instructions in its OPEN marker.
```

## Repo layout

```
wormhole/
├── pyproject.toml
├── README.md
├── src/wormhole/
│   ├── cli.py             # click entry: fold / unfold / status
│   ├── config.py          # toml loader
│   ├── pane.py            # tty detection (stdio → ppid walk), pane_key
│   ├── store.py           # notes.jsonl + wormhole.md writer
│   └── adapters/
│       ├── base.py        # ABC: find_session, read_turns
│       ├── claude.py
│       ├── concord.py     # reads concord-sessions/*.jsonl written by accord
│       ├── gemini.py
│       ├── kiro.py
│       └── opencode.py
└── tests/
```
