#!/usr/bin/env bash
set -euo pipefail

# Compatibility front-end for the requested command shape:
#   hermes start jarvis
#   hermes stop jarvis
#
# Hermes' native plugin CLI surface is plugin-scoped, so the actual plugin
# commands are `hermes jarvis start|stop`. This shim translates only the two
# Jarvis lifecycle forms and delegates every other command unchanged.

SELF="${BASH_SOURCE[0]}"
SELF_REALPATH="$(python3 - <<'PY' "$SELF"
import os, sys
print(os.path.realpath(sys.argv[1]))
PY
)"

find_real_hermes() {
  if [[ -n "${HERMES_REAL_BIN:-}" && -x "${HERMES_REAL_BIN}" ]]; then
    printf '%s\n' "${HERMES_REAL_BIN}"
    return 0
  fi

  local entry real
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    if [[ "$entry" != /* ]]; then
      continue
    fi
    [[ ! -x "$entry" ]] && continue
    real="$(python3 - <<'PY' "$entry"
import os, sys
print(os.path.realpath(sys.argv[1]))
PY
)"
    if [[ "$real" != "$SELF_REALPATH" ]]; then
      printf '%s\n' "$entry"
      return 0
    fi
  done < <(type -a -p hermes 2>/dev/null | awk '!seen[$0]++')

  return 1
}

REAL_HERMES="$(find_real_hermes || true)"
if [[ -z "$REAL_HERMES" ]]; then
  echo "Error: the Jarvis Hermes shim could not locate the real 'hermes' executable." >&2
  echo "Set HERMES_REAL_BIN=/path/to/hermes and retry." >&2
  exit 127
fi

if [[ "${1:-}" == "start" && "${2:-}" == "jarvis" ]]; then
  shift 2
  exec "$REAL_HERMES" jarvis start "$@"
fi

if [[ "${1:-}" == "stop" && "${2:-}" == "jarvis" ]]; then
  shift 2
  exec "$REAL_HERMES" jarvis stop "$@"
fi

exec "$REAL_HERMES" "$@"
