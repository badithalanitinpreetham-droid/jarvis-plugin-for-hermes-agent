"""Jarvis-owned lifecycle for TencentDB Agent Memory and its local Ollama backend.

Jarvis owns the process lifecycle without replacing Tencent's deployment scripts:
- ensure the pinned TencentDB source exists
- start Ollama only when Jarvis started it
- start memory-core -> memory-hub -> proxy
- persist ownership so ``hermes start jarvis`` and ``hermes stop jarvis`` work
  across separate CLI processes
- stop the Tencent stack only when Jarvis started/owns it
- persist logs/state under HERMES_HOME/.jarvis

No credentials are hard-coded. Ollama is used through its OpenAI-compatible endpoint.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

TENCENT_REPO = "https://github.com/TencentCloud/TencentDB-Agent-Memory.git"
TENCENT_PIN = "439f22ace03a08de828597b4eea2661f0978510c"
DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"


class TencentRuntime:
    """Own the local TencentDB/Ollama runtime for one Jarvis host."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started = False
        self._ollama_process: Optional[subprocess.Popen] = None
        self._root: Optional[Path] = None
        self._tencent_root: Optional[Path] = None
        self._ollama_owned = False
        self._tencent_owned = False

    @staticmethod
    def _home(explicit: Optional[str] = None) -> Path:
        return Path(explicit or os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _run(cmd: list[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None,
             timeout: int = 300, log_file: Optional[Path] = None) -> None:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        stdout = stderr = None
        handle = None
        try:
            if log_file:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                handle = log_file.open("a", encoding="utf-8")
                stdout = stderr = handle
            result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged,
                                    stdout=stdout, stderr=stderr, text=True, timeout=timeout,
                                    check=False)
        finally:
            if handle:
                handle.close()
        if result.returncode != 0:
            raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}")

    @staticmethod
    def _state_path(home: Path) -> Path:
        return home / ".jarvis" / "runtime-state.json"

    @classmethod
    def _load_state(cls, home: Path) -> dict[str, object]:
        try:
            raw = json.loads(cls._state_path(home).read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @classmethod
    def _save_state(cls, home: Path, state: dict[str, object]) -> None:
        path = cls._state_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def _clear_state(cls, home: Path) -> None:
        try:
            cls._state_path(home).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _ensure_tencent_source(self, home: Path) -> Path:
        target = home / ".jarvis" / "tencentdb" / "source"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target / ".git").exists():
            self._run(["git", "clone", "--depth", "1", TENCENT_REPO, str(target)], timeout=300)
        self._run(["git", "fetch", "--quiet", "origin", TENCENT_PIN], cwd=target, timeout=120)
        self._run(["git", "checkout", "--detach", TENCENT_PIN], cwd=target, timeout=60)
        return target

    @staticmethod
    def _env_file(tencent_root: Path, model: str) -> Path:
        deploy = tencent_root / "deploy" / "global-images"
        deploy.mkdir(parents=True, exist_ok=True)
        env_file = deploy / ".env"
        lines = [
            "MEMORY_CORE_IMAGE=agentmemory/memory-core:latest",
            "MEMORY_HUB_IMAGE=agentmemory/memory-hub:latest",
            "PROXY_IMAGE=agentmemory/memory-proxy:latest",
            f"MEMORY_LLM_BASE_URL={DEFAULT_OLLAMA_URL}",
            "MEMORY_LLM_API_KEY=ollama",
            f"MEMORY_LLM_MODEL={model}",
            "MEMORY_LLM_PROTOCOL=openai",
            "PROXY_UPSTREAM_URL=" + DEFAULT_OLLAMA_URL,
            "PROXY_UPSTREAM_API_KEY=ollama",
            f"PROXY_UPSTREAM_MODEL={model}",
            "MEMORY_CORE_PORT=8420",
            "PANEL_PORT=8125",
            "KNOWLEDGE_PORT=8424",
            "PROXY_PORT=8096",
            "KNOWLEDGE_PUBLIC_BASE_URL=http://host.docker.internal:8424/v3",
            "MEMORY_CORE_VOLUME=tdai-memory-core-data",
            "PANEL_VOLUME=tdai-panel-data",
            "MEMORY_CORE_GATEWAY_API_KEY=",
            "MEMORY_CORE_ADMIN_USERNAME=admin",
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_file

    @staticmethod
    def _process_command(pid: int) -> str:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=3, check=False,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _start_ollama(self, home: Path, model: str) -> None:
        if self._port_open("127.0.0.1", 11434):
            self._ollama_owned = False
            return
        exe = os.environ.get("JARVIS_OLLAMA_EXECUTABLE", "ollama")
        try:
            log = home / ".jarvis" / "logs" / "ollama.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            handle = log.open("ab")
            self._ollama_process = subprocess.Popen(
                [exe, "serve"], stdout=handle, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            handle.close()
            self._ollama_owned = True
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(
                "Jarvis could not start Ollama. Install Ollama or set JARVIS_OLLAMA_EXECUTABLE."
            ) from exc
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._port_open("127.0.0.1", 11434):
                self._persist_runtime_state(home)
                return
            if self._ollama_process.poll() is not None:
                raise RuntimeError("Ollama exited while Jarvis was starting it.")
            time.sleep(0.5)
        raise RuntimeError("Ollama did not become ready on 127.0.0.1:11434 within 30 seconds.")

    def _persist_runtime_state(self, home: Path) -> None:
        state: dict[str, object] = {
            "version": 1,
            "hermes_home": str(home),
            "tencent_root": str(self._tencent_root) if self._tencent_root else "",
            "tencent_owned": self._tencent_owned,
            "ollama_owned": self._ollama_owned,
            "ollama_pid": self._ollama_process.pid if self._ollama_process is not None else None,
            "ollama_pgid": self._ollama_process.pid if self._ollama_process is not None else None,
        }
        self._save_state(home, state)

    def start(self, hermes_home: Optional[str] = None) -> None:
        with self._lock:
            if self._started:
                return
            if os.environ.get("JARVIS_TENCENT_AUTOSTART", "1").lower() in {"0", "false", "no"}:
                self._started = True
                return
            home = self._home(hermes_home)
            model = os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            previous = self._load_state(home)
            self._root = home
            self._tencent_root = Path(str(previous.get("tencent_root") or "")).expanduser() if previous.get("tencent_root") else None
            if self._tencent_root is None or not self._tencent_root.exists():
                self._tencent_root = self._ensure_tencent_source(home)

            self._start_ollama(home, model)
            deploy = self._tencent_root / "deploy" / "global-images"
            self._env_file(self._tencent_root, model)
            log = home / ".jarvis" / "logs" / "tencent-runtime.log"

            already_running = all([
                self._port_open("127.0.0.1", 8420),
                self._port_open("127.0.0.1", 8125),
                self._port_open("127.0.0.1", 8096),
            ])
            if previous.get("tencent_owned") is True or not already_running:
                for script in ("start-memory-core.sh", "start-memory-hub.sh", "start-proxy.sh"):
                    self._run(["bash", script], cwd=deploy, timeout=300, log_file=log)
                self._tencent_owned = True
            else:
                self._tencent_owned = False

            self._started = True
            self._persist_runtime_state(home)

    def stop(self, hermes_home: Optional[str] = None) -> None:
        with self._lock:
            home = self._root or self._home(hermes_home)
            state = self._load_state(home)
            tencent_root_value = state.get("tencent_root") or self._tencent_root
            tencent_root = Path(str(tencent_root_value)).expanduser() if tencent_root_value else None
            tencent_owned = bool(state.get("tencent_owned") or self._tencent_owned)

            if tencent_owned and tencent_root is not None:
                deploy = tencent_root / "deploy" / "global-images"
                script = deploy / "stop-all.sh"
                if script.is_file():
                    try:
                        self._run(["bash", str(script)], cwd=deploy, timeout=120,
                                  log_file=home / ".jarvis" / "logs" / "tencent-runtime.log")
                    except Exception:
                        pass

            ollama_owned = bool(state.get("ollama_owned") or self._ollama_owned)
            ollama_pid_value = state.get("ollama_pid")
            ollama_pgid_value = state.get("ollama_pgid")
            if ollama_owned and ollama_pid_value:
                try:
                    pid = int(ollama_pid_value)
                    pgid = int(ollama_pgid_value or pid)
                except (TypeError, ValueError):
                    pid = pgid = 0
                if pid > 0 and "ollama" in self._process_command(pid).lower():
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except OSError:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except OSError:
                            pass
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and self._process_command(pid):
                        time.sleep(0.25)

            proc = self._ollama_process
            self._ollama_process = None
            if proc is not None and proc.poll() is None and self._ollama_owned:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            self._started = False
            self._ollama_owned = False
            self._tencent_owned = False
            self._root = None
            self._tencent_root = None
            self._clear_state(home)

    def status(self, hermes_home: Optional[str] = None) -> dict[str, object]:
        home = self._root or self._home(hermes_home)
        state = self._load_state(home)
        return {
            "started": self._started,
            "ollama_reachable": self._port_open("127.0.0.1", 11434),
            "ollama_owned_by_jarvis": bool(state.get("ollama_owned") or self._ollama_owned),
            "tencent_owned_by_jarvis": bool(state.get("tencent_owned") or self._tencent_owned),
            "tencent_source": str(state.get("tencent_root") or self._tencent_root or ""),
            "memory_core_reachable": self._port_open("127.0.0.1", 8420),
            "memory_hub_reachable": self._port_open("127.0.0.1", 8125),
            "proxy_reachable": self._port_open("127.0.0.1", 8096),
        }

    def close(self) -> None:
        self.stop()


_RUNTIME = TencentRuntime()


def get_tencent_runtime() -> TencentRuntime:
    return _RUNTIME
