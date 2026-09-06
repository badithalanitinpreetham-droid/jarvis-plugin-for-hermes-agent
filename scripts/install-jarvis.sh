#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required to provision the bundled TencentDB Agent Memory source." >&2
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
  2. Starts Ollama if Ollama is not already running.
  3. Pulls the Jarvis/Tencent local models if they are missing.
  4. Configures Tencent memory-core, memory-hub and proxy to use Ollama.
  5. Starts the TencentDB services.

Default local models:
  Memory/LLM: qwen3.5:4b
  Embeddings: snowflake-arctic-embed2

Model files remain installed in Ollama after `hermes stop jarvis`.
Jarvis stops only the Ollama server process that Jarvis itself started.

TencentDB source is kept under:
  ~/.hermes/.jarvis/tencentdb/source
EOF