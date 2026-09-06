"""Jarvis-owned lifecycle for TencentDB Agent Memory and its local Ollama backend.

Jarvis owns the process lifecycle without replacing Tencent's deployment scripts:
- ensure the pinned TencentDB source exists
- start Ollama only when Jarvis started it
- start memory-core -> memory-hub -> proxy
- stop the Tencent stack and only the Ollama process started by Jarvis
- persist logs/state under HERMES_HOME/.jarvis so the package is self-contained

No credentials are hard-coded. Ollama is used through its OpenAI-compatible endpoint.
"""
from __future__ import annotations

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

    def _ensure_tencent_source(self, home: Path) -> Path:
        target = home / ".jarvis" / "tencentdb" / "source"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target / ".git").exists():
            self._run(["git", "clone", "--depth", "1", TENCENT_REPO, str(target)], timeout=300)
        # Use the repository's pinned revision so Jarvis and Tencent code stay reproducible.
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

    def _start_ollama(self, home: Path, model: str) -> None:
        if self._port_open("127.0.0.1", 11434):
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
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(
                "Jarvis could not start Ollama. Install Ollama or set JARVIS_OLLAMA_EXECUTABLE."
            ) from exc
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._port_open("127.0.0.1", 11434):
                return
            if self._ollama_process.poll() is not None:
                raise RuntimeError("Ollama exited while Jarvis was starting it.")
            time.sleep(0.5)
        raise RuntimeError("Ollama did not become ready on 127.0.0.1:11434 within 30 seconds.")

    def start(self, hermes_home: Optional[str] = None) -> None:
        with self._lock:
            if self._started:
                return
            if os.environ.get("JARVIS_TENCENT_AUTOSTART", "1").lower() in {"0", "false", "no"}:
                self._started = True
                return
            home = self._home(hermes_home)
            model = os.environ.get("JARVIS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            self._root = home
            self._tencent_root = self._ensure_tencent_source(home)
            self._start_ollama(home, model)
            deploy = self._tencent_root / "deploy" / "global-images"
            self._env_file(self._tencent_root, model)
            log = home / ".jarvis" / "logs" / "tencent-runtime.log"
            for script in ("start-memory-core.sh", "start-memory-hub.sh", "start-proxy.sh"):
                self._run(["bash", script], cwd=deploy, timeout=300, log_file=log)
            self._started = True

    def stop(self) -> None:
        with self._lock:
            if self._tencent_root is not None:
                deploy = self._tencent_root / "deploy" / "global-images"
                script = deploy / "stop-all.sh"
                if script.is_file():
                    try:
                        self._run(["bash", str(script)], cwd=deploy, timeout=120,
                                  log_file=(self._root or Path.home()) / ".jarvis" / "logs" / "tencent-runtime.log")
                    except Exception:
                        pass
            proc = self._ollama_process
            self._ollama_process = None
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._started = False

    def status(self) -> dict[str, object]:
        return {
            "started": self._started,
            "ollama_reachable": self._port_open("127.0.0.1", 11434),
            "tencent_source": str(self._tencent_root) if self._tencent_root else "",
            "memory_core_reachable": self._port_open("127.0.0.1", 8420),
            "memory_hub_reachable": self._port_open("127.0.0.1", 8125),
            "proxy_reachable": self._port_open("127.0.0.1", 8096),
        }

    def close(self) -> None:
        self.stop()


_RUNTIME = TencentRuntime()


def get_tencent_runtime() -> TencentRuntime:
    return _RUNTIME
