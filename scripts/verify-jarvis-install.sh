#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/TencentDB-Agent-Memory"
PIN="439f22ace03a08de828597b4eea2661f0978510c"

if [[ ! -f "$ROOT/.gitmodules" ]]; then
  echo "ERROR: .gitmodules is missing." >&2
  exit 1
fi
if [[ ! -e "$VENDOR" ]]; then
  echo "ERROR: TencentDB Agent Memory submodule directory is missing." >&2
  exit 1
fi
if [[ ! -d "$VENDOR/.git" && ! -f "$VENDOR/.git" ]]; then
  echo "ERROR: TencentDB Agent Memory is not populated. Run scripts/jarvis-tencent-memory.sh." >&2
  exit 1
fi
actual="$(git -C "$VENDOR" rev-parse HEAD 2>/dev/null || true)"
if [[ "$actual" != "$PIN" ]]; then
  echo "ERROR: TencentDB Agent Memory revision mismatch." >&2
  echo "Expected: $PIN" >&2
  echo "Actual:   ${actual:-<missing>}" >&2
  exit 1
fi

echo "Jarvis plugin source: OK"
echo "TencentDB Agent Memory: OK"
echo "Pinned revision: $actual"
