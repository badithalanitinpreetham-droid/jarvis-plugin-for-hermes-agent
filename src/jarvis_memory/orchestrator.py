"""Zero-config local bootstrap for Ollama and MemoryCore."""
from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get("JARVIS_EMBEDDING_MODEL", "kinfra-text-embedding-0.6b")
GENERATIVE_MODEL = os.environ.get("JARVIS_GENERATIVE_MODEL", "qwen3.5:0.5b")
OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
MEMORYCORE_REF = os.environ.get("JARVIS_MEMORYCORE_REF", "2ee22397f6091b8cd3ea847bc1edb04d3bec0c94")

_spawned_processes: list[subprocess.Popen] = []


def _cleanup() -> None:
    for proc in list(_spawned_processes):
        try:
            if proc.poll() is None:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
        except Exception:
            logger.debug("Background process cleanup failed", exc_info=True)


atexit.register(_cleanup)


def _run(cmd: list[str], *, cwd: str | None = None, check: bool = True, capture: bool = False):
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def is_ollama_running() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_API_URL}/api/version", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def start_ollama() -> None:
    if is_ollama_running():
        return
    try:
        kwargs = {"start_new_session": True} if sys.platform != "win32" else {}
        proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        _spawned_processes.append(proc)
    except FileNotFoundError as exc:
        raise RuntimeError("Ollama is not installed or not on PATH") from exc
    for _ in range(15):
        if is_ollama_running():
            return
        time.sleep(1)
    raise RuntimeError("Ollama did not become ready within 15 seconds")


def _node_version_ok() -> bool:
    try:
        result = _run(["node", "--version"], capture=True)
        raw = result.stdout.strip().lstrip("v")
        major, minor, *_ = (int(x) for x in raw.split("."))
        return (major, minor) >= (22, 16)
    except Exception:
        return False


def _ensure_gateway(gateway_dir: str) -> None:
    parent = os.path.dirname(gateway_dir)
    os.makedirs(parent, exist_ok=True)
    if not os.path.isdir(os.path.join(gateway_dir, ".git")):
        _run(["git", "clone", "https://github.com/TencentCloud/TencentDB-Agent-Memory.git", gateway_dir])
    fetch = _run(
        ["git", "fetch", "--depth", "1", "origin", MEMORYCORE_REF],
        cwd=gateway_dir,
        check=False,
        capture=True,
    )
    if fetch.returncode != 0:
        raise RuntimeError(f"Unable to fetch MemoryCore ref {MEMORYCORE_REF}: {fetch.stderr.strip()[:500]}")
    checkout = _run(
        ["git", "checkout", "--detach", MEMORYCORE_REF],
        cwd=gateway_dir,
        check=False,
        capture=True,
    )
    if checkout.returncode != 0:
        raise RuntimeError(f"Unable to checkout MemoryCore ref {MEMORYCORE_REF}: {checkout.stderr.strip()[:500]}")
    if not _node_version_ok():
        raise RuntimeError("MemoryCore requires Node.js >= 22.16")

    package_json = os.path.join(gateway_dir, "package.json")
    package_lock = os.path.join(gateway_dir, "package-lock.json")
    node_modules = os.path.join(gateway_dir, "node_modules")
    if not os.path.isdir(node_modules):
        _run(["npm", "ci" if os.path.isfile(package_lock) else "install"], cwd=gateway_dir)

    build_script = False
    try:
        with open(package_json, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        build_script = isinstance(data, dict) and isinstance(data.get("scripts"), dict) and "build" in data["scripts"]
    except (OSError, json.JSONDecodeError):
        build_script = False
    if build_script and not os.path.isdir(os.path.join(gateway_dir, "dist")):
        _run(["npm", "run", "build"], cwd=gateway_dir)

    os.environ["GATEWAY_START_CMD"] = "npm start"
    os.environ["GATEWAY_CWD"] = gateway_dir


def pull_model(name: str) -> None:
    wanted = str(name).strip()
    if not wanted:
        return
    result = _run(["ollama", "list"], capture=True, check=False)
    if result.returncode == 0:
        installed = set()
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if fields:
                installed.add(fields[0])
        if wanted in installed:
            return
    _run(["ollama", "pull", wanted])


def configure_zero_config_env() -> None:
    os.environ.setdefault("TDAI_LLM_BASE_URL", f"{OLLAMA_API_URL}/v1")
    os.environ.setdefault("TDAI_LLM_MODEL", GENERATIVE_MODEL)
    os.environ.setdefault("TDAI_EMBEDDING_MODEL", EMBEDDING_MODEL)
    os.environ.setdefault("TDAI_LLM_API_KEY", "ollama-local")
    os.environ.setdefault("TDAI_API_VERSION", "v3")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    start_ollama()
    pull_model(EMBEDDING_MODEL)
    pull_model(GENERATIVE_MODEL)
    configure_zero_config_env()
    _ensure_gateway(os.path.expanduser("~/.jarvis-memory/tencent-gateway"))
    from .server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
