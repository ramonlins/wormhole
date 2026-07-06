#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Installing wormhole package..."
if command -v pipx >/dev/null; then
  pipx install --editable . >/dev/null
elif pip install -e . >/dev/null 2>&1; then
  : # succeeded (older pip or venv)
else
  echo "    pip install failed (externally-managed env). Trying: sudo apt install -y pipx"
  sudo apt install -y pipx >/dev/null
  pipx install --editable . >/dev/null
fi

if ! command -v wh >/dev/null; then
  echo "ERROR: 'wh' not found on PATH after install."
  echo "Check that pip's bin directory is on PATH (e.g. ~/.local/bin)."
  exit 1
fi

WH_PATH="$(command -v wh)"
echo "    wh installed at: $WH_PATH"

echo
echo "==> Detecting AI CLIs on PATH..."
for cli in claude codex gemini opencode hermes kiro; do
  if command -v "$cli" >/dev/null; then
    echo "    [found]    $cli ($(command -v $cli))"
  else
    echo "    [missing]  $cli — install separately if you want to use it"
  fi
done

cat <<EOF

==> Done. Per-CLI first-run steps (human-required):

  Claude    - just run '! wh fold' from inside Claude. Hook auto-installs.
  Codex     - run 'wh fold' from a Codex shell/tool call. It writes
              <repo>/.codex/hooks.json; open '/hooks' in Codex, trust it,
              then restart Codex if the hook list was already loaded.
  Gemini    - first open prompts to trust the folder. Approve, then
              '/quit' and reopen so settings reload. Run '! wh fold'.
  OpenCode  - 'pip install' already wrote the plugin spec. Reopen OpenCode
              so the plugin loads, then '! wh fold' to mark streaming.
  Hermes    - run '! wh fold' inside hermes (installer pre-seeds consent),
              then quit & reopen hermes so it re-registers shell hooks.
              Needs PyYAML ('pip install pyyaml').
  Kiro      - run '! wh fold' inside kiro, then exit & reopen so the agent
              config reloads. See SKILL guide for details.

  '! wh fold'   opens the channel and installs that CLI's hook.
  '! wh unfold' closes it and removes the hook.

  Watch shared context:    cat .wormhole.md
  Pane state:              wh status

EOF
