#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Stop the native runtime and unload macOS launchd before removing the package.
# This keeps a loaded watchdog from recreating services after uninstall.
if command -v hermes >/dev/null 2>&1; then
  hermes stop jarvis >/dev/null 2>&1 || true
  hermes config set memory.provider "" >/dev/null 2>&1 || true
  hermes plugins disable jarvis >/dev/null 2>&1 || true
fi

python3 -m pip uninstall -y jarvis-memory || true

echo "Jarvis package disabled/uninstalled."
echo "The pinned TencentDB source in $ROOT/vendor/TencentDB-Agent-Memory is intentionally retained as repository content."
echo "Jarvis experience data, Ollama models, and TencentDB volumes are also retained."
