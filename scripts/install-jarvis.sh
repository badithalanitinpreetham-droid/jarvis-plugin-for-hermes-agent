#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required to provision the pinned TencentDB Agent Memory source." >&2
  exit 1
fi

"$ROOT/scripts/jarvis-tencent-memory.sh"
python3 -m pip install -e "$ROOT"

# Complete the Hermes integration when this installer is run inside a Hermes
# environment. Installation still succeeds when Hermes is not installed yet.
if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable jarvis >/dev/null 2>&1 || true
  hermes config set memory.provider jarvis >/dev/null 2>&1 || true
fi

echo
cat <<'EOF'
Jarvis is installed in the current Python environment.

Primary lifecycle:
  hermes start jarvis
  hermes stop jarvis

Normal Hermes usage:
  hermes

When Jarvis starts, it automatically:
  1. Reuses or provisions the pinned TencentDB Agent Memory source.
  2. Starts Ollama only if Ollama is not already running.
  3. Ensures the configured Jarvis/Tencent memory model exists in Ollama.
  4. Configures Tencent memory-core, memory-hub and proxy to use Ollama.
  5. Starts only missing TencentDB services in dependency order.
  6. On macOS, installs the user-level launchd supervisor for crash recovery.

Default Jarvis/Tencent memory model:
  qwen3.5:4b

The model is used by Jarvis/TencentDB memory services and does not replace
Hermes' primary AIAgent model/provider.

Model files remain installed in Ollama after `hermes stop jarvis`.
Jarvis stops only the Ollama server process that Jarvis itself started.

TencentDB source is kept under:
  ~/.hermes/.jarvis/tencentdb/source

macOS launchd service:
  ~/Library/LaunchAgents/com.jarvis.hermes-runtime.plist
EOF
