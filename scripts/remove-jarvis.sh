#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHIM_PATH="$HOME/.local/bin/hermes"

# Stop the native runtime and unload macOS launchd before removing the package.
# This keeps a loaded watchdog from recreating services after uninstall.
if command -v hermes >/dev/null 2>&1; then
  hermes stop jarvis >/dev/null 2>&1 || true
  hermes config set memory.provider "" >/dev/null 2>&1 || true
  hermes plugins disable jarvis >/dev/null 2>&1 || true
fi

python3 -m pip uninstall -y jarvis-memory || true

# Remove only the Jarvis-installed command shim. Never replace or delete the
# user's real Hermes executable. Keep the PATH block because removing it is a
# separate shell-profile policy decision and may have pre-existed this plugin.
if [[ -f "$SHIM_PATH" ]] && grep -Fq 'jarvis-hermes-shim' "$SHIM_PATH" 2>/dev/null; then
  rm -f "$SHIM_PATH"
fi

echo "Jarvis package disabled/uninstalled."
echo "The pinned TencentDB source in $ROOT/vendor/TencentDB-Agent-Memory is intentionally retained as repository content."
echo "Jarvis experience data, Ollama models, and TencentDB volumes are also retained."
