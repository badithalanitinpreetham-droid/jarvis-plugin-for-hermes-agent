"""Jarvis-owned lifecycle for TencentDB Agent Memory and its local Ollama backend.

Jarvis owns the local runtime used by its memory provider:
- provision the pinned TencentDB source once per Hermes home
- start Ollama only when Jarvis needs it and preserve ownership across CLI processes
- ensure the memory LLM and embedding models exist in Ollama
- start memory-core -> memory-hub -> proxy
- persist ownership so ``hermes start jarvis`` / ``hermes stop jarvis`` work across processes
- stop only resources that the persisted Jarvis state says it owns

The actual Tencent deployment remains Tencent's supported shell scripts.
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
DEFAULT_EMBEDDING_MODEL = "snowflake-arctic-embed2"


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
    def _run(
        cmd: list[str],
        *,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        timeout: int = 300,
        log_file: Optional[Path] = None,
    ) -> None:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        stdout = stderr = None
        handle = None
        try:
            if log_file:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                handle = log_file.open("ab")
                stdout = stderr = handle
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=merged,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout,
                check=False,
            )
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
        except (FileNotFoundError, OSError):
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
    def _env_file(tencent_root: Path, model: str, embedding_model: str) -> Path:
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
            f"EMBEDDING_BASE_URL={DEFAULT_OLLAMA_URL}",
            "EMBEDDING_API_KEY=ollama",
            f"EMBEDDING_MODEL={embedding_model}",
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
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _previous_ollama_is_still_owned(self, state: dict[str, object]) -> bool:
        if state.get("ollama_owned") is not True:
            return False
        value = state.get("ollama_pid")
        try:
            pid = int(value) if value is not None else 0
        except (TypeError, ValueError):
            return False
        return pid > 0 and "ollama" in self._process_command(pid).lower()

    @staticmethod
    def _ollama_model_available(executable: str, model: str) -> bool:
        try:
            result = subprocess.run(
                [executable, "show", model],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False

    def _ensure_ollama_models(self, home: Path, executable: str, models: list[str]) -> None:
        log = home / ".jarvis" / "logs" / "ollama-pull.log"
        seen: set[str] = set()
        for model in models:
            model = model.strip()
            if not model or model in seen:
                continue
            seen.add(model)
            if self._ollama_model_available(executable, model):
                continue
            self._run([executable, "pull", model], timeout=1800, log_file=log)

    def _start_ollama(
        self,
        home: Path,
        model: str,
        embedding_model: str,
        previous: dict[str, object],
    ) -> None:
        exe = os.environ.get("JARVIS_OLLAMA_EXECUTABLE", "ollama")
        if self._port_open("127.0.0.1", 11434):
            self._ollama_owned = self._previous_ollama_is_still_owned(previous)
            self._ensure_ollama_models(home, exe, [model, embedding_model])
            return

        try:
            log = home / ".jarvis" / "logs" / "ollama.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            handle = log.open("ab")
            self._ollama_process = subprocess.Popen(
                [exe, "serve"],
                stdout=handle,
                stderr=subprocess.STDOUT,
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
                self._ensure_ollama_models(home, exe, [model, embedding_model])
                return
            if self._ollama_process.poll() is not None:
                raise RuntimeError("Ollama exited while Jarvis was starting it.")
            time.sleep(0.5)
        raise RuntimeError("Ollama did not become ready on 127.0.0.1:11434 within 30 seconds.")

    def _persist_runtime_state(self, home: Path, *, enabled: bool = True) -> None:
        state: dict[str, object] = {
            "version": 2,
            "enabled": enabled,
            "hermes_home": str(home),
            "tencent_root": str(self._tencent_root) if self._tencent_root else "",
            "tencent_owned": self._tencent_owned if enabled else False,
            "ollama_owned": self._ollama_owned if enabled else False,
            "ollama_pid": self._ollama_process.pid if enabled and self._ollama_process is not None else None,
            "ollama_pgid": self._ollama_process.pid if enabled and self._ollama_process is not None else None,
            "ollama_model": os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            "embedding_model": os.environ.get("JARVIS_OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        }
        # Preserve the last known cross-process Ollama owner PID. This is essential because
        # ``hermes start jarvis`` and ``hermes stop jarvis`` are separate OS processes.
        if enabled and self._ollama_process is None:
            previous = self._load_state(home)
            if previous.get("ollama_owned") is True:
                state["ollama_owned"] = True
                state["ollama_pid"] = previous.get("ollama_pid")
                state["ollama_pgid"] = previous.get("ollama_pgid")
        self._save_state(home, state)

    def start(self, hermes_home: Optional[str] = None, *, force: bool = False) -> None:
        with self._lock:
            if self._started:
                return
            if (
                os.environ.get("JARVIS_TENCENT_AUTOSTART", "1").lower() in {"0", "false", "no"}
                and not force
            ):
                self._started = True
                return

            home = self._home(hermes_home)
            previous = self._load_state(home)
            if previous.get("enabled") is False and not force:
                self._root = home
                self._started = True
                return

            model = os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            embedding_model = os.environ.get("JARVIS_OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
            self._root = home
            previous_root = previous.get("tencent_root")
            self._tencent_root = Path(str(previous_root)).expanduser() if previous_root else None
            if self._tencent_root is None or not self._tencent_root.exists():
                self._tencent_root = self._ensure_tencent_source(home)

            self._start_ollama(home, model, embedding_model, previous)
            deploy = self._tencent_root / "deploy" / "global-images"
            self._env_file(self._tencent_root, model, embedding_model)
            log = home / ".jarvis" / "logs" / "tencent-runtime.log"

            services_up = all(
                self._port_open("127.0.0.1", port)
                for port in (8420, 8125, 8096)
            )
            if services_up:
                # Repeated starts are idempotent. Preserve an existing Jarvis ownership claim
                # instead of needlessly restarting/removing healthy Tencent containers.
                self._tencent_owned = bool(previous.get("tencent_owned") is True)
            else:
                for script in ("start-memory-core.sh", "start-memory-hub.sh", "start-proxy.sh"):
                    self._run(["bash", script], cwd=deploy, timeout=300, log_file=log)
                self._tencent_owned = True

            self._started = True
            self._persist_runtime_state(home, enabled=True)

    def stop(self, hermes_home: Optional[str] = None, *, disable: bool = False) -> None:
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
                        self._run(
                            ["bash", str(script)],
                            cwd=deploy,
                            timeout=120,
                            log_file=home / ".jarvis" / "logs" / "tencent-runtime.log",
                        )
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
            self._root = home
            self._tencent_root = tencent_root
            self._persist_runtime_state(home, enabled=not disable)
            if not disable and not self._tencent_root:
                self._clear_state(home)

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
            "embedding_model": state.get("embedding_model") or os.environ.get(
                "JARVIS_OLLAMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
            ),
            "memory_core_reachable": memory_core,
            "memory_hub_reachable": memory_hub,
            "proxy_reachable": proxy,
        }

    def close(self) -> None:
        self.stop()


_RUNTIME = TencentRuntime()


def get_tencent_runtime() -> TencentRuntime:
    return _RUNTIME
