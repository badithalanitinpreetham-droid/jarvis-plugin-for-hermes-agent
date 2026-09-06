"""Jarvis-owned lifecycle for TencentDB Agent Memory and its local Ollama backend."""
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
        handle = None
        try:
            if log_file:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                handle = log_file.open("ab")
                result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged,
                                        stdout=handle, stderr=handle, timeout=timeout, check=False)
            else:
                result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged,
                                        timeout=timeout, check=False)
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
            result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                    capture_output=True, text=True, timeout=3, check=False)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _kill_process_group(pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    @staticmethod
    def _ollama_model_available(executable: str, model: str) -> bool:
        try:
            result = subprocess.run([executable, "show", model], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=20, check=False)
            return result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False

    def _ensure_ollama_model(self, home: Path, executable: str, model: str) -> None:
        if not self._ollama_model_available(executable, model):
            self._run([executable, "pull", model], timeout=1800,
                      log_file=home / ".jarvis" / "logs" / "ollama-pull.log")

    def _previous_ollama_is_still_owned(self, state: dict[str, object]) -> bool:
        if state.get("ollama_owned") is not True:
            return False
        value = state.get("ollama_pid")
        try:
            pid = int(value) if value is not None else 0
        except (TypeError, ValueError):
            return False
        return pid > 0 and "ollama" in self._process_command(pid).lower()

    def _start_ollama(self, home: Path, model: str, previous: dict[str, object]) -> None:
        exe = os.environ.get("JARVIS_OLLAMA_EXECUTABLE", "ollama")
        if self._port_open("127.0.0.1", 11434):
            self._ollama_owned = self._previous_ollama_is_still_owned(previous)
            self._ensure_ollama_model(home, exe, model)
            return
        log = home / ".jarvis" / "logs" / "ollama.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = log.open("ab")
            self._ollama_process = subprocess.Popen([exe, "serve"], stdout=handle,
                                                     stderr=subprocess.STDOUT, start_new_session=True)
            handle.close()
            self._ollama_owned = True
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError("Jarvis could not start Ollama. Install Ollama or set JARVIS_OLLAMA_EXECUTABLE.") from exc
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._port_open("127.0.0.1", 11434):
                self._ensure_ollama_model(home, exe, model)
                return
            if self._ollama_process.poll() is not None:
                raise RuntimeError("Ollama exited while Jarvis was starting it.")
            time.sleep(0.5)
        raise RuntimeError("Ollama did not become ready on 127.0.0.1:11434 within 30 seconds.")

    def _persist_runtime_state(self, home: Path, *, enabled: bool = True) -> None:
        previous = self._load_state(home)
        state: dict[str, object] = {
            "version": 3,
            "enabled": enabled,
            "hermes_home": str(home),
            "tencent_root": str(self._tencent_root) if self._tencent_root else str(previous.get("tencent_root") or ""),
            "tencent_owned": self._tencent_owned if enabled else False,
            "ollama_owned": self._ollama_owned if enabled else False,
            "ollama_pid": self._ollama_process.pid if enabled and self._ollama_process is not None else previous.get("ollama_pid"),
            "ollama_pgid": self._ollama_process.pid if enabled and self._ollama_process is not None else previous.get("ollama_pgid"),
            "ollama_model": os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        }
        self._save_state(home, state)

    def _rollback_start(self, home: Path) -> None:
        if self._tencent_owned and self._tencent_root is not None:
            self._stop_tencent(self._tencent_root / "deploy" / "global-images",
                               home / ".jarvis" / "logs" / "tencent-runtime.log")
        if self._ollama_owned and self._ollama_process is not None:
            self._kill_process_group(self._ollama_process.pid)
        self._ollama_process = None
        self._ollama_owned = False
        self._tencent_owned = False
        self._started = False

    def start(self, hermes_home: Optional[str] = None, *, force: bool = False) -> None:
        with self._lock:
            if self._started:
                return
            if (os.environ.get("JARVIS_TENCENT_AUTOSTART", "1").lower() in {"0", "false", "no"}) and not force:
                self._started = True
                return
            home = self._home(hermes_home)
            previous = self._load_state(home)
            if previous.get("enabled") is False and not force:
                self._root = home
                self._started = True
                return
            model = os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            self._root = home
            previous_root = previous.get("tencent_root")
            self._tencent_root = Path(str(previous_root)).expanduser() if previous_root else self._ensure_tencent_source(home)
            try:
                self._start_ollama(home, model, previous)
                deploy = self._tencent_root / "deploy" / "global-images"
                self._env_file(self._tencent_root, model)
                log = home / ".jarvis" / "logs" / "tencent-runtime.log"
                services_up = all(self._port_open("127.0.0.1", port) for port in (8420, 8125, 8096))
                if services_up:
                    self._tencent_owned = previous.get("tencent_owned") is True
                else:
                    for script in ("start-memory-core.sh", "start-memory-hub.sh", "start-proxy.sh"):
                        self._run(["bash", script], cwd=deploy, timeout=300, log_file=log)
                        # Once the first Tencent component has started, the whole stack is
                        # considered Jarvis-owned for rollback, even if a later component fails.
                        self._tencent_owned = True
                self._started = True
                self._persist_runtime_state(home, enabled=True)
            except Exception:
                self._rollback_start(home)
                raise

    def _stop_tencent(self, deploy: Path, log: Path) -> None:
        script = deploy / "stop-all.sh"
        if script.is_file():
            try:
                self._run(["bash", str(script)], cwd=deploy, timeout=120, log_file=log)
            except Exception:
                pass

    def stop(self, hermes_home: Optional[str] = None, *, disable: bool = False) -> None:
        with self._lock:
            home = self._root or self._home(hermes_home)
            state = self._load_state(home)
            tencent_root_value = state.get("tencent_root") or self._tencent_root
            tencent_root = Path(str(tencent_root_value)).expanduser() if tencent_root_value else None
            if bool(state.get("tencent_owned") or self._tencent_owned) and tencent_root is not None:
                self._stop_tencent(tencent_root / "deploy" / "global-images",
                                   home / ".jarvis" / "logs" / "tencent-runtime.log")
            ollama_owned = bool(state.get("ollama_owned") or self._ollama_owned)
            ollama_pid_value = state.get("ollama_pid")
            if ollama_owned and ollama_pid_value:
                try:
                    pid = int(ollama_pid_value)
                except (TypeError, ValueError):
                    pid = 0
                if pid > 0 and "ollama" in self._process_command(pid).lower():
                    self._kill_process_group(pid)
            if self._ollama_process is not None and self._ollama_owned:
                self._kill_process_group(self._ollama_process.pid)
            self._ollama_process = None
            self._started = False
            self._ollama_owned = False
            self._tencent_owned = False
            self._root = home
            self._tencent_root = tencent_root
            self._persist_runtime_state(home, enabled=not disable)

    def status(self, hermes_home: Optional[str] = None) -> dict[str, object]:
        home = self._root or self._home(hermes_home)
        state = self._load_state(home)
        memory_core = self._port_open("127.0.0.1", 8420)
        memory_hub = self._port_open("127.0.0.1", 8125)
        proxy = self._port_open("127.0.0.1", 8096)
        return {
            "enabled": state.get("enabled", True),
            "started": bool(memory_core and memory_hub and proxy),
            "process_active": self._started,
            "ollama_reachable": self._port_open("127.0.0.1", 11434),
            "ollama_owned_by_jarvis": bool(state.get("ollama_owned") or self._ollama_owned),
            "tencent_owned_by_jarvis": bool(state.get("tencent_owned") or self._tencent_owned),
            "tencent_source": str(state.get("tencent_root") or self._tencent_root or ""),
            "ollama_model": state.get("ollama_model") or os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            "memory_core_reachable": memory_core,
            "memory_hub_reachable": memory_hub,
            "proxy_reachable": proxy,
        }

    def close(self) -> None:
        self.stop()


_RUNTIME = TencentRuntime()


def get_tencent_runtime() -> TencentRuntime:
    return _RUNTIME
