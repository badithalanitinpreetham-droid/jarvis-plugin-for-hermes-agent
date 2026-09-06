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

# Hermes plugins expose plugin-scoped CLI commands (`hermes jarvis start`).
# Install a tiny compatibility front-end in a dedicated Jarvis-owned
# directory. Never overwrite the real Hermes executable.
SHIM_DIR="$HOME/.hermes/bin"
SHIM_PATH="$SHIM_DIR/hermes"
mkdir -p "$SHIM_DIR"
cp "$ROOT/scripts/hermes-jarvis-shim.sh" "$SHIM_PATH"
chmod +x "$SHIM_PATH"

add_path_block() {
  local rc_file="$1"
  [[ -f "$rc_file" ]] || touch "$rc_file"
  if ! grep -Fq '# >>> jarvis-hermes-shim >>>' "$rc_file" 2>/dev/null; then
    cat >> "$rc_file" <<'EOF'

# >>> jarvis-hermes-shim >>>
export PATH="$HOME/.hermes/bin:$PATH"
# <<< jarvis-hermes-shim <<<
EOF
  fi
}

# zsh is the default shell on modern macOS; bash is covered for users who
# explicitly use it. Existing shells are not modified in-place; open a new
# shell (or source the relevant rc file) after installation.
case "${SHELL:-}" in
  */zsh) add_path_block "$HOME/.zshrc" ;;
  */bash) add_path_block "$HOME/.bashrc" ;;
  *)
    add_path_block "$HOME/.zshrc"
    add_path_block "$HOME/.bashrc"
    ;;
esac

# Ensure the current installer process can verify the native plugin path even
# before the user opens a new shell.
export PATH="$SHIM_DIR:$PATH"

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable jarvis >/dev/null 2>&1 || true
  hermes config set memory.provider jarvis >/dev/null 2>&1 || true
fi

echo
cat <<'EOF'
Jarvis is installed in the current Python environment.

Primary native Hermes lifecycle:
  hermes jarvis start
  hermes jarvis stop

Requested compatibility lifecycle:
  hermes start jarvis
  hermes stop jarvis

The compatibility form is a thin user-level wrapper installed at:
  ~/.hermes/bin/hermes

It translates only the two Jarvis lifecycle forms into the native plugin
commands. Every other Hermes command is passed unchanged to the real Hermes
executable, which is never overwritten.

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

Model files remain installed in Ollama after Jarvis is stopped.
Jarvis stops only the Ollama server process that Jarvis itself started.

TencentDB source is kept under:
  ~/.hermes/.jarvis/tencentdb/source

macOS launchd service:
  ~/Library/LaunchAgents/com.jarvis.hermes-runtime.plist
EOF
