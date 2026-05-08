# Wormhole Setup

Activate when the user asks to "set up wormhole", "install wormhole hooks",
"configure wormhole", "make wormhole work in <claude|gemini|kiro>", or any
variant of getting an AI CLI to participate in the shared per-pane channel.

## What this skill is for

Wormhole shares per-pane context across AI CLIs by writing to
`<cwd>/.wormhole.md` (a symlink to `~/vault/wormhole/panes/<pane_key>/wormhole.md`).
Each CLI installs a hook so every assistant turn auto-folds its session into
that file. Other CLIs in the same pane read the file before responding.

This skill walks an agent through the steps required to make its host CLI a
participant. Pick the section matching the CLI you are running in.

## Hook reference (the parts that bit us)

| CLI     | Hook event(s)              | Settings file                          | Scope     | Tool config required |
|---------|----------------------------|----------------------------------------|-----------|----------------------|
| Claude  | `Stop`                     | `~/.claude/settings.json`              | user      | no                   |
| Concord | in-process (post-judge)    | n/a — code-driven                      | n/a       | n/a                  |
| Gemini  | `AfterAgent`               | `<cwd>/.gemini/settings.json`          | workspace | no                   |
| Kiro    | `stop` + `agentSpawn`      | `~/.kiro/agents/kiro_default.json`     | user      | yes (`tools`)        |

Wrong event name = silent no-op. Wrong settings file = silent no-op. Both
mistakes look identical to "the hook doesn't work", so always verify the
event name and the file path before debugging anything else.

## Common prerequisites (do once per host)

1. From the wormhole repo, run `./setup.sh` (or `pip install -e .`).
2. Confirm `wh` is on PATH: `command -v wh`. If missing, ensure pip's bin
   directory (typically `~/.local/bin`) is on PATH.
3. Confirm `AGENT.md` exists in the project's working directory. CLIs auto-load
   it on session start; it instructs them to read `.wormhole.md` first.

`wh` is invoked from inside a CLI via its shell escape (e.g. `! wh fold`). The
first manual `wh fold` from a given CLI both opens the channel and installs
that CLI's hook so subsequent turns flow through automatically.

## Claude Code

- **Hook event:** `Stop` (fires once per assistant turn after the model
  finishes responding). Claude exposes other events too — `PreToolUse`,
  `UserPromptSubmit`, etc. — but `Stop` is the only one that gives a clean
  "turn finished, fold now" signal.
- **Settings file:** `~/.claude/settings.json`, user-level. Once installed the
  hook applies to every project on this host where `wh` is on PATH.
- **No tool registration needed.** Claude resolves tools at runtime; the agent
  always has access to file reads.
- **No trust prompt.** Claude does not gate hooks on workspace trust.

Steps:

1. Open Claude in the project directory.
2. Run `! wh fold` inside Claude. This writes `.wormhole.md`, marks `claude`
   as streaming for this pane, and adds the `Stop` matcher in
   `~/.claude/settings.json`.
3. Verify the next turn auto-folds: `cat .wormhole.md` should grow on each
   assistant response.

`! wh unfold` closes the channel; if no other pane is still streaming claude,
it also removes the matcher from `settings.json`.

## Gemini CLI

- **Hook event:** `AfterAgent` (fires after each assistant turn). NOT `Stop`
  — that is Claude's name; wiring `Stop` into Gemini's settings looks valid
  but silently does nothing because Gemini never dispatches that key.
- **Settings file:** `<cwd>/.gemini/settings.json`, workspace-level **only**.
  Gemini ignores `~/.gemini/settings.json` for hooks — internally it loads
  hooks exclusively from `settings.workspace.settings.hooks`. Writing the
  user-level file looks correct on disk and never fires at runtime. This is
  the single biggest source of "hook installed but doesn't work" reports.
- **Trust-folder gate:** even with the right file, Gemini won't run hooks
  until the workspace is approved as trusted. The prompt appears on first
  open. Until you approve and reload, hooks are silently suppressed.
- **No tool registration needed.**

Steps:

1. Open Gemini in the project directory.
2. Approve the trust-folder prompt the first time it appears.
3. `/quit` and reopen Gemini so settings reload (trust state and hook config
   are cached for the lifetime of the process).
4. Run `! wh fold` inside Gemini. This installs the `AfterAgent` hook into
   `<cwd>/.gemini/settings.json`.
5. On the next assistant turn, `cat .wormhole.md` should show a fresh
   `[gemini ...]` block. If not, the workspace is not trusted yet — repeat
   2–3.

## Concord

Concord (binary: `accord`) is structurally different from the other CLIs.
It doesn't write a session log on disk for wormhole to read; instead, the
Rust binary calls into wormhole directly after each `/accord` judge
synthesis, writing one jsonl record + triggering an `--auto` fold. So
"installing the hook" is just "running a recent enough concord build" —
no settings file to edit.

- **Trigger:** in-process. After every successful judge synthesis,
  concord appends to `~/vault/wormhole/panes/<pane_key>/concord-sessions/
  <run_id>.jsonl` and shells out `wh fold concord --auto`.
- **Pane key:** sha1[:8] of the git project root, computed identically
  on both sides (Rust + Python). Both halves agree on the file path
  without coordination.
- **Block content:** only the judge's prose verdict. The full debate
  metadata (agree / conflict / unique) stays in the jsonl for forensics
  but doesn't pollute `wormhole.md` for downstream consumers.
- **Workers stay private.** Concord fans out to 3-5 workers per turn;
  none of them publish. Only the synthesis lands in the channel — that's
  the whole point of running concord in front of the wormhole.
- **No external hook to install or remove.** `! wh fold concord` marks
  the pane as streaming concord; `! wh unfold` unmarks. Future syntheses
  silent-no-op when the pane isn't streaming.
- **Session id is fixed (`"concord"`).** Every fold replaces the prior
  block in `wormhole.md` instead of stacking. The per-run filename on
  disk is only for forensics.

Steps:

1. Open accord in the project directory.
2. Run `! wh fold` from accord's prompt input. This opens the channel
   with a `[concord session=concord ...]` placeholder block — channel
   is now live; first synthesis replaces the placeholder.
3. Run `/accord <prompt>`. The judge's verdict lands in `wormhole.md`,
   replacing the placeholder. Other CLIs in the same pane (claude, kiro,
   etc.) reading `.wormhole.md` will see it as context.
4. Continue chatting. Each `/accord` turn updates the same block.
5. `! wh unfold` when done. Closes the channel; concord stops folding.

Common-issue specific to concord:

- **`! wh fold` errors with "no concord sessions dir".** You're on a
  build older than concord v0.1.0-beta.1 + wormhole v0.3.0-beta.1.
  The placeholder behavior was added there. Update both.
- **Block text is JSON instead of prose.** Same — wormhole v0.3.0-beta.1
  added the JSON-envelope unwrap.
- **Empty `concord-sessions/` folders appear in unrelated panes.** Older
  adapter pre-created the dir. Fixed in v0.3.0-beta.1; clean up the
  empty dirs and they won't return.

## Kiro

Kiro is the most fragile of the three. Things that bit us, in order:

- **Hook events:** `stop` (lowercase — not `Stop`) for fold, plus `agentSpawn`
  for AGENT.md injection. We tried `userPromptSubmit` first; it works but
  Kiro echoes that hook's stdout to the chat every turn, which is noisy.
  `agentSpawn` fires once at session start and is silent in the chat, which
  is what we want for `cat AGENT.md`.
- **Settings file:** `~/.kiro/agents/kiro_default.json`. This is an *agent
  config*, not a settings.json — schema differs from Claude/Gemini. Hooks go
  under `hooks.<event>` as a list of `{"command": "..."}` objects.
- **`name` + `description` are required.** Kiro's loader rejects an agent
  config that lacks `name` with `invalid agent config: kiro_default.json`,
  and the agent silently won't load. The installer seeds `name` (from the
  filename stem) and a default `description` on first creation; if you see
  the rejection on a pre-existing file, add those two keys by hand. Schema
  reference: `~/.kiro/agents/agent_config.json.example`.
- **No `KIRO.md` auto-load.** Claude auto-loads `CLAUDE.md`, Gemini
  auto-loads `GEMINI.md`; Kiro has no equivalent convention. Without the
  `agentSpawn` `cat AGENT.md` hook, the agent never sees the directive to
  read `.wormhole.md` first.
- **Tool registration is mandatory.** Kiro agents start with **zero tools
  registered** unless the config declares them. With zero tools the model
  fabricates `<tool_call>` XML in plain text and invents the response —
  the symptom is a chat reply that *looks* like a tool was called but no
  read ever happened, often followed by a fake ENOENT-style error.
- **Tool naming gotcha.** The agent config uses *aliases* (`read`, `write`,
  `shell`); the binary internals use full names (`fs_read`, `fs_write`,
  `execute_bash`). Always write the alias form into the config — the full
  names won't match. Source: `~/.kiro/agents/agent_config.json.example`.
- `wh fold` writes `tools: ["read"]` and `allowedTools: ["read"]` so the
  agent can load `.wormhole.md` without per-call confirmation.
- **Empty-session "no turns found".** Kiro touches a fresh
  `~/.kiro/sessions/cli/<uuid>.jsonl` the moment a session opens, and shell
  escapes (`!wh fold`) are not logged as `Prompt` events. A naive
  "latest by mtime" pick lands on that empty file and errors with
  `no turns found in kiro session <uuid>`. The adapter now skips files with
  no `Prompt`/`AssistantMessage` events and falls back to the most recent
  file that has them — so the prior completed session gets folded instead of
  the empty current one.

Steps:

1. Open Kiro in the project directory.
2. Run `! wh fold` inside Kiro. This:
   - Adds the `stop` hook (`wh fold --auto`) and `agentSpawn` hook
     (`cat AGENT.md`) to `~/.kiro/agents/kiro_default.json`.
   - Registers `tools: ["read"]` and `allowedTools: ["read"]`.
3. Exit and reopen Kiro so the agent config reloads. **This step is not
   optional** — Kiro caches the agent config at boot.
4. Verify by inspecting the latest session JSONL at
   `~/.kiro/sessions/cli/<id>.jsonl`. Assistant events should contain
   `kind: "toolUse"` with `name: "read"` operating on `.wormhole.md`. If you
   only see plain text mentioning `<tool_call>` XML, the agent config did
   not reload (or `tools` is missing) — restart Kiro and re-check the file.

## Verification (any CLI)

- `wh status` — pane key, detected TTY, last fold event for this pane.
- `cat .wormhole.md` — current shared context. The header line should read
  `<!-- WORMHOLE:OPEN pane=... -->`. `WORMHOLE:CLOSED` means someone unfolded.
- `wh unfold` — close the channel for this pane; if no other pane streams the
  same source, removes that CLI's hook.

## Common issues

- **Hook installed but never fires.** The CLI needs a restart so the new
  settings/agent config reload. Gemini and Kiro almost always need this on
  first install.
- **`.wormhole.md` shows `WORMHOLE:CLOSED`.** Someone ran `wh unfold`. Run
  `wh fold` again to reopen and reinstall the hook for the current CLI.
- **Kiro replies with hallucinated `<tool_call>` XML and an ENOENT-style
  error.** Tools were not registered. Re-run `! wh fold` from inside Kiro to
  rewrite `tools`/`allowedTools`, then restart Kiro.
- **`invalid agent config: kiro_default.json`.** The agent config is missing
  `name` (and/or `description`). On a fresh install the wormhole installer
  seeds both; on a pre-existing file you hand-edit them in. The `name`
  conventionally matches the filename stem (`kiro_default`).
- **`no turns found in kiro session <uuid>` from `wh fold` / `wh fold kiro`.**
  Kiro created the JSONL for the current session but hasn't logged any
  `Prompt` events yet (often because the only "prompt" so far was a `!wh fold`
  shell escape, which Kiro doesn't log). The current adapter skips empty
  candidates; if you still see this, every JSONL under
  `~/.kiro/sessions/cli/` is empty — have a normal turn with Kiro first,
  then fold.
- **Gemini hook silently does nothing.** The workspace is not trusted, or the
  hook was written to user-level settings. The hook MUST live in
  `<cwd>/.gemini/settings.json`, and the trust prompt must have been approved
  before that session started.
- **`wh: command not found` inside a hook.** Hooks often run with a stripped
  PATH. `wh fold` writes the absolute path of `wh` resolved at install time;
  if you moved the binary, reinstall (`pip install -e .` then `! wh fold`).
