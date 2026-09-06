#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required to provision the bundled TencentDB Agent Memory source." >&2
  exit 1
fi

"$ROOT/scripts/jarvis-tencent-memory.sh"
python3 -m pip install -e "$ROOT"

echo
cat <<'EOF'
Jarvis is installed in the current Python environment.

Enable the native Hermes plugin:
  hermes plugins enable jarvis

Select Jarvis as the Hermes memory provider:
  hermes config set memory.provider jarvis

Start Jarvis and its owned local services:
  hermes start jarvis

Stop Jarvis and its owned local services:
  hermes stop jarvis

TencentDB Agent Memory source is pinned under:
  vendor/TencentDB-Agent-Memory

The Tencent source is provisioned once and reused. Jarvis owns the runtime
lifecycle; no separate start-all.sh or `ollama serve` command is required for
normal operation.
EOF