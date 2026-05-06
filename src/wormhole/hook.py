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
