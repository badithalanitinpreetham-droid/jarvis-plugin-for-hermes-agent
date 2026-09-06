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

Hermes plugin:
  hermes plugins enable jarvis

Jarvis memory provider:
  hermes config set memory.provider jarvis

TencentDB Agent Memory source is pinned under:
  vendor/TencentDB-Agent-Memory

Note: source provisioning does not start MemoryCore/Hub/Proxy automatically.
Use the bundled Tencent deployment files when you want the local services running.
EOF
