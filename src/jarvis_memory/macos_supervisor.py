"""macOS launchd supervisor for the Jarvis-owned local runtime."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .tencent_runtime import get_tencent_runtime

LABEL = "com.jarvis.hermes-runtime"
CHECK_INTERVAL = 20.0


def _home(explicit: Optional[str] = None) -> Path:
    return Path(explicit or os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _target() -> str:
    return f"{_domain()}/{LABEL}"


def _run_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False, timeout=30)


def _log_dir(home: Path) -> Path:
    path = home / ".jarvis" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _xml(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _plist_content(home: Path) -> str:
    log = _log_dir(home) / "macos-supervisor.log"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>
<string>{_xml(sys.executable)}</string><string>-m</string><string>jarvis_memory.macos_supervisor</string>
<string>--home</string><string>{_xml(str(home))}</string>
</array>
<key>WorkingDirectory</key><string>{_xml(str(home))}</string>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>{_xml(str(log))}</string>
<key>StandardErrorPath</key><string>{_xml(str(log))}</string>
<key>EnvironmentVariables</key><dict><key>HERMES_HOME</key><string>{_xml(str(home))}</string></dict>
</dict></plist>\n'''


def install(home: Optional[str] = None) -> bool:
    if sys.platform != "darwin":
        return False
    root = _home(home)
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plist_content(root), encoding="utf-8")
    _run_launchctl("bootout", _target())
    result = _run_launchctl("bootstrap", _domain(), str(path))
    if result.returncode != 0:
        raise RuntimeError(f"launchd bootstrap failed: {result.stderr.strip() or result.stdout.strip()}")
    result = _run_launchctl("kickstart", "-k", _target())
    if result.returncode != 0:
        raise RuntimeError(f"launchd kickstart failed: {result.stderr.strip() or result.stdout.strip()}")
    return True


def uninstall() -> bool:
    if sys.platform != "darwin":
        return False
    result = _run_launchctl("bootout", _target())
    if result.returncode != 0 and "Could not find service" not in (result.stderr or ""):
        raise RuntimeError(f"launchd bootout failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        _plist_path().unlink()
    except FileNotFoundError:
        pass
    return True


def status() -> dict[str, object]:
    if sys.platform != "darwin":
        return {"supported": False, "loaded": False}
    result = _run_launchctl("print", _target())
    return {"supported": True, "loaded": result.returncode == 0, "target": _target()}


def run(home: Optional[str] = None) -> int:
    if sys.platform != "darwin":
        return 0
    root = _home(home)
    runtime = get_tencent_runtime()
    log = _log_dir(root) / "macos-supervisor.log"
    while True:
        state = runtime._load_state(root)
        if state.get("enabled", True) is False:
            return 0
        try:
            current = runtime.status(str(root))
            healthy = all(bool(current.get(key)) for key in (
                "ollama_reachable", "memory_core_reachable", "memory_hub_reachable", "proxy_reachable"))
            if not healthy:
                runtime.start(str(root), force=True)
        except Exception as exc:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"supervisor recovery error: {exc}\n")
        time.sleep(CHECK_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis macOS launchd supervisor")
    parser.add_argument("--home", default=None)
    parser.add_argument("action", nargs="?", choices=["run", "install", "uninstall", "status"], default="run")
    args = parser.parse_args()
    if args.action == "install":
        install(args.home); return 0
    if args.action == "uninstall":
        uninstall(); return 0
    if args.action == "status":
        print(status()); return 0
    return run(args.home)


if __name__ == "__main__":
    raise SystemExit(main())
