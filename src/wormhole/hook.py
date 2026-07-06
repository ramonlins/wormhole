from __future__ import annotations

import json
import shutil
from pathlib import Path

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
# Resolve `wh` at import time so each host writes its own absolute path into
# Claude/Gemini settings and the OpenCode plugin file. Falling back to bare
# "wh" lets PATH-driven setups still work, but hooks often run with a stripped
# PATH so an absolute path is preferable when available.
HOOK_COMMAND = f"{shutil.which('wh') or 'wh'} fold --auto"

# Each CLI names its "agent loop completed" event differently.
# Claude Code uses "Stop"; Gemini CLI uses "AfterAgent". Wiring both into the
# same key would silently no-op on whichever CLI does not recognize it.
CLAUDE_EVENT = "Stop"
GEMINI_EVENT = "AfterAgent"
CODEX_EVENT = "Stop"

OPENCODE_SETTINGS = "opencode.json"
OPENCODE_PLUGIN_PATH = ".wormhole/opencode.ts"
OPENCODE_PLUGIN_CONTENT = f"""import type {{ Plugin }} from "@opencode-ai/plugin"

export const WormholePlugin: Plugin = async ({{ $ }}) => ({{
  event: async ({{ event }}) => {{
    if (event.type === "session.idle") {{
      $`{HOOK_COMMAND}`.nothrow().quiet()
    }}
  }}
}})
"""
CODEX_HOOKS_PATH = ".codex/hooks.json"
CODEX_STATUS_MESSAGE = "Folding Codex session into wormhole"


# Gemini CLI ignores user-level (~/.gemini/settings.json) for hooks: it loads
# only workspace settings (`<cwd>/.gemini/settings.json`) and gates them behind
# a trusted-folder check. Writing user-level hooks looks like it works but they
# are silently never fired. See gemini-cli bundle: `projectHooks: settings.workspace.settings.hooks`.
def gemini_settings_path(cwd: Path) -> Path:
    return cwd / ".gemini" / "settings.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _project_root(cwd: Path) -> Path:
    cwd = cwd.resolve()
    for d in [cwd, *cwd.parents]:
        if (d / ".git").exists():
            return d
    return cwd


def _install(settings_path: Path, event: str) -> bool:
    data = _load(settings_path)
    hooks = data.setdefault("hooks", {})
    matchers = hooks.setdefault(event, [])
    for m in matchers:
        for h in m.get("hooks", []) or []:
            if h.get("command") == HOOK_COMMAND:
                return False
    matchers.append({"matcher": "*", "hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    _save(settings_path, data)
    return True


def _remove(settings_path: Path, event: str) -> bool:
    data = _load(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    matchers = hooks.get(event) or []
    if not matchers:
        return False
    changed = False
    new_matchers: list[dict] = []
    for m in matchers:
        original = m.get("hooks", []) or []
        kept = [h for h in original if h.get("command") != HOOK_COMMAND]
        if len(kept) != len(original):
            changed = True
        if kept:
            new_matchers.append({**m, "hooks": kept})
    if not changed:
        return False
    if new_matchers:
        hooks[event] = new_matchers
    else:
        hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    _save(settings_path, data)
    return True


def _is_installed(settings_path: Path, event: str) -> bool:
    data = _load(settings_path)
    hooks = data.get("hooks") or {}
    for m in hooks.get(event) or []:
        for h in m.get("hooks", []) or []:
            if h.get("command") == HOOK_COMMAND:
                return True
    return False


def install_claude() -> bool:
    return _install(CLAUDE_SETTINGS, CLAUDE_EVENT)


def remove_claude() -> bool:
    return _remove(CLAUDE_SETTINGS, CLAUDE_EVENT)


def claude_installed() -> bool:
    return _is_installed(CLAUDE_SETTINGS, CLAUDE_EVENT)


def install_gemini(cwd: Path) -> bool:
    return _install(gemini_settings_path(cwd), GEMINI_EVENT)


def remove_gemini(cwd: Path) -> bool:
    return _remove(gemini_settings_path(cwd), GEMINI_EVENT)


def gemini_installed(cwd: Path) -> bool:
    return _is_installed(gemini_settings_path(cwd), GEMINI_EVENT)


def codex_hooks_path(cwd: Path) -> Path:
    return _project_root(cwd) / CODEX_HOOKS_PATH


def _codex_hook_handler() -> dict:
    return {
        "type": "command",
        "command": HOOK_COMMAND,
        "timeout": 30,
        "statusMessage": CODEX_STATUS_MESSAGE,
    }


def install_codex(cwd: Path) -> bool:
    hooks_path = codex_hooks_path(cwd)
    data = _load(hooks_path)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected JSON root in {hooks_path}; expected object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError(f"unexpected `hooks` value in {hooks_path}; expected object")
    groups = hooks.setdefault(CODEX_EVENT, [])
    if not isinstance(groups, list):
        raise RuntimeError(
            f"unexpected `hooks.{CODEX_EVENT}` value in {hooks_path}; expected list"
        )
    for group in groups:
        if not isinstance(group, dict):
            continue
        for handler in group.get("hooks", []) or []:
            if isinstance(handler, dict) and handler.get("command") == HOOK_COMMAND:
                return False
    groups.append({"hooks": [_codex_hook_handler()]})
    _save(hooks_path, data)
    return True


def remove_codex(cwd: Path) -> bool:
    hooks_path = codex_hooks_path(cwd)
    data = _load(hooks_path)
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get(CODEX_EVENT)
    if not isinstance(groups, list):
        return False

    changed = False
    kept_groups: list[dict] = []
    for group in groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        original = group.get("hooks", []) or []
        kept = [
            h for h in original
            if not (isinstance(h, dict) and h.get("command") == HOOK_COMMAND)
        ]
        if len(kept) != len(original):
            changed = True
        if kept:
            kept_groups.append({**group, "hooks": kept})
    if not changed:
        return False
    if kept_groups:
        hooks[CODEX_EVENT] = kept_groups
    else:
        hooks.pop(CODEX_EVENT, None)
    if not hooks:
        data.pop("hooks", None)
    _save(hooks_path, data)
    return True


def codex_installed(cwd: Path) -> bool:
    data = _load(codex_hooks_path(cwd))
    hooks = data.get("hooks") if isinstance(data, dict) else None
    groups = hooks.get(CODEX_EVENT) if isinstance(hooks, dict) else None
    if not isinstance(groups, list):
        return False
    return any(
        isinstance(handler, dict) and handler.get("command") == HOOK_COMMAND
        for group in groups
        if isinstance(group, dict)
        for handler in (group.get("hooks", []) or [])
    )


# Kiro hooks live in agent config files (~/.kiro/agents/<name>.json).
# We inject into the global default agent config so the hook fires in any session.
KIRO_AGENT_DIR = Path.home() / ".kiro" / "agents"
KIRO_AGENT_FILE = KIRO_AGENT_DIR / "kiro_default.json"

# Kiro has no filename convention (no KIRO.md auto-load), so we inject the
# AGENT.md directive via an agentSpawn hook — fires once when the kiro agent
# starts, mirroring how CLAUDE.md / GEMINI.md auto-load at session start.
# We avoid userPromptSubmit because kiro echoes that hook's stdout to the
# terminal every turn, which is noisy.
KIRO_SPAWN_COMMAND = "cat AGENT.md 2>/dev/null || true"

# Kiro agents start with zero tools unless the config declares them. Without
# `tools`, the model fabricates `<tool_call>` XML in plain text and invents
# the response. We register `read` (the alias for fs_read) so the agent can
# actually load .wormhole.md, and trust it via `allowedTools` so reads run
# without per-call confirmation. Tool aliases come from kiro's example config
# (~/.kiro/agents/agent_config.json.example).
KIRO_TOOLS = ["read"]
KIRO_ALLOWED_TOOLS = ["read"]


def _kiro_hook_set(hooks: dict, event: str, command: str) -> bool:
    entries = hooks.setdefault(event, [])
    if any(h.get("command") == command for h in entries):
        return False
    entries.append({"command": command})
    return True


def _kiro_hook_unset(hooks: dict, event: str, command: str) -> bool:
    entries = hooks.get(event, [])
    kept = [h for h in entries if h.get("command") != command]
    if len(kept) == len(entries):
        return False
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
    return True


def install_kiro(cwd: Path) -> bool:
    data = _load(KIRO_AGENT_FILE)
    # Kiro's loader rejects agent configs missing `name` (and treats it as the
    # agent identity). Seed it on first creation; never overwrite a user value.
    changed = False
    if not data.get("name"):
        data["name"] = KIRO_AGENT_FILE.stem
        changed = True
    if "description" not in data:
        data["description"] = "Default kiro agent — wormhole-managed hooks."
        changed = True
    hooks = data.setdefault("hooks", {})
    changed |= _kiro_hook_set(hooks, "stop", HOOK_COMMAND)
    changed |= _kiro_hook_set(hooks, "agentSpawn", KIRO_SPAWN_COMMAND)
    # Older versions wrote a userPromptSubmit hook with the same command;
    # remove it on re-install so users don't keep seeing per-turn output.
    changed |= _kiro_hook_unset(hooks, "userPromptSubmit", KIRO_SPAWN_COMMAND)
    tools = data.setdefault("tools", [])
    for t in KIRO_TOOLS:
        if t not in tools:
            tools.append(t)
            changed = True
    allowed = data.setdefault("allowedTools", [])
    for t in KIRO_ALLOWED_TOOLS:
        if t not in allowed:
            allowed.append(t)
            changed = True
    if not changed:
        return False
    _save(KIRO_AGENT_FILE, data)
    return True


def remove_kiro(cwd: Path) -> bool:
    data = _load(KIRO_AGENT_FILE)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = _kiro_hook_unset(hooks, "stop", HOOK_COMMAND)
    changed |= _kiro_hook_unset(hooks, "agentSpawn", KIRO_SPAWN_COMMAND)
    changed |= _kiro_hook_unset(hooks, "userPromptSubmit", KIRO_SPAWN_COMMAND)
    if not changed:
        return False
    if not hooks:
        data.pop("hooks", None)
    _save(KIRO_AGENT_FILE, data)
    return True


def kiro_installed(cwd: Path) -> bool:
    hooks = _load(KIRO_AGENT_FILE).get("hooks", {})
    stop_ok = any(h.get("command") == HOOK_COMMAND for h in hooks.get("stop", []))
    spawn_ok = any(
        h.get("command") == KIRO_SPAWN_COMMAND for h in hooks.get("agentSpawn", [])
    )
    return stop_ok and spawn_ok


def install_opencode(cwd: Path) -> bool:
    plugin_path = cwd / OPENCODE_PLUGIN_PATH
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(OPENCODE_PLUGIN_CONTENT)

    settings_path = cwd / OPENCODE_SETTINGS
    data = _load(settings_path)
    plugins = data.setdefault("plugin", [])
    plugin_ref = f"./{OPENCODE_PLUGIN_PATH}"
    if plugin_ref not in plugins:
        plugins.append(plugin_ref)
        _save(settings_path, data)
        return True
    return False


def remove_opencode(cwd: Path) -> bool:
    settings_path = cwd / OPENCODE_SETTINGS
    data = _load(settings_path)
    plugins = data.get("plugin")
    if not isinstance(plugins, list):
        return False

    plugin_ref = f"./{OPENCODE_PLUGIN_PATH}"
    if plugin_ref in plugins:
        plugins.remove(plugin_ref)
        if not plugins:
            data.pop("plugin", None)
        _save(settings_path, data)

        plugin_path = cwd / OPENCODE_PLUGIN_PATH
        if plugin_path.exists():
            plugin_path.unlink()
        return True
    return False


def opencode_installed(cwd: Path) -> bool:
    settings_path = cwd / OPENCODE_SETTINGS
    data = _load(settings_path)
    plugins = data.get("plugin")
    if not isinstance(plugins, list):
        return False
    return f"./{OPENCODE_PLUGIN_PATH}" in plugins


# Hermes shell hooks live under `hooks:` in $HERMES_HOME/config.yaml (or
# ~/.hermes/config.yaml). Closest "turn end" event hermes exposes is
# `post_llm_call` — fires once per LLM response, guarded on non-empty
# non-interrupted responses (mirrors Claude's `Stop`).
HERMES_EVENT = "post_llm_call"
# Hermes also gates unseen (event, command) pairs behind a TTY consent prompt
# and persists approvals to shell-hooks-allowlist.json. From a shell escape
# inside hermes the prompt has no stdin, so we pre-seed the allowlist entry
# at install time and clean it up on remove.
HERMES_ALLOWLIST_NAME = "shell-hooks-allowlist.json"


def _hermes_home() -> Path:
    import os
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def hermes_config_path() -> Path:
    return _hermes_home() / "config.yaml"


def hermes_allowlist_path() -> Path:
    return _hermes_home() / HERMES_ALLOWLIST_NAME


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as e:  # pragma: no cover — soft-dep error path
        raise RuntimeError(
            "hermes adapter needs PyYAML to edit ~/.hermes/config.yaml. "
            "Install it with `pip install pyyaml`."
        ) from e
    return yaml


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    yaml = _yaml()
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_yaml(path: Path, data: dict) -> None:
    yaml = _yaml()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _seed_hermes_allowlist(command: str) -> None:
    path = hermes_allowlist_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        data = {}
    approvals = data.setdefault("approvals", [])
    for entry in approvals:
        if (
            isinstance(entry, dict)
            and entry.get("event") == HERMES_EVENT
            and entry.get("command") == command
        ):
            return
    from datetime import datetime, timezone
    approvals.append({
        "event": HERMES_EVENT,
        "command": command,
        "approved_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _drop_hermes_allowlist(command: str) -> None:
    path = hermes_allowlist_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    approvals = data.get("approvals")
    if not isinstance(approvals, list):
        return
    kept = [
        e for e in approvals
        if not (
            isinstance(e, dict)
            and e.get("event") == HERMES_EVENT
            and e.get("command") == command
        )
    ]
    if kept == approvals:
        return
    if kept:
        data["approvals"] = kept
    else:
        data.pop("approvals", None)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_hermes() -> bool:
    cfg_path = hermes_config_path()
    data = _load_yaml(cfg_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        # Existing `hooks:` value is malformed — refuse to clobber it.
        raise RuntimeError(
            f"unexpected `hooks` value in {cfg_path}; expected mapping"
        )
    entries = hooks.setdefault(HERMES_EVENT, [])
    if not isinstance(entries, list):
        raise RuntimeError(
            f"unexpected `hooks.{HERMES_EVENT}` value in {cfg_path}; expected list"
        )
    for entry in entries:
        if isinstance(entry, dict) and entry.get("command") == HOOK_COMMAND:
            # Already installed — still make sure consent is pre-seeded.
            _seed_hermes_allowlist(HOOK_COMMAND)
            return False
    entries.append({"command": HOOK_COMMAND, "timeout": 30})
    _save_yaml(cfg_path, data)
    _seed_hermes_allowlist(HOOK_COMMAND)
    return True


def remove_hermes() -> bool:
    cfg_path = hermes_config_path()
    if not cfg_path.exists():
        return False
    data = _load_yaml(cfg_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    entries = hooks.get(HERMES_EVENT)
    if not isinstance(entries, list):
        return False
    kept = [
        e for e in entries
        if not (isinstance(e, dict) and e.get("command") == HOOK_COMMAND)
    ]
    if kept == entries:
        return False
    if kept:
        hooks[HERMES_EVENT] = kept
    else:
        hooks.pop(HERMES_EVENT, None)
    if not hooks:
        data.pop("hooks", None)
    _save_yaml(cfg_path, data)
    _drop_hermes_allowlist(HOOK_COMMAND)
    return True


def hermes_installed() -> bool:
    cfg_path = hermes_config_path()
    if not cfg_path.exists():
        return False
    hooks = _load_yaml(cfg_path).get("hooks") or {}
    entries = hooks.get(HERMES_EVENT) if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(e, dict) and e.get("command") == HOOK_COMMAND for e in entries
    )
