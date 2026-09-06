#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/TencentDB-Agent-Memory"
PIN="439f22ace03a08de828597b4eea2661f0978510c"

if [[ ! -d "$VENDOR/.git" ]]; then
  git clone https://github.com/TencentCloud/TencentDB-Agent-Memory.git "$VENDOR"
fi

git -C "$VENDOR" fetch --quiet origin "$PIN"
git -C "$VENDOR" checkout --detach "$PIN"

echo "TencentDB Agent Memory provisioned at: $VENDOR"
echo "Pinned revision: $PIN"
