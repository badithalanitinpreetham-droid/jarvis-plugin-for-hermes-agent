"""
Jarvis Memory Orchestrator

This is the "Zero-Config" bootstrapper for the Jarvis package.
Before starting the MCP server, it silently ensures that the local AI
infrastructure (Ollama) is running and the required models are downloaded.
It then auto-wires the environment variables so the TencentDB Gateway
uses the local Ollama instance for memory distillation.
"""

import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
import atexit
from typing import List

from .server import main as server_main

logger = logging.getLogger(__name__)

# The models to auto-pull for the zero-config setup
EMBEDDING_MODEL = "kinfra-text-embedding-0.6b"
# A lightweight generative model is also needed by the Gateway for memory summarization
GENERATIVE_MODEL = "qwen3.5:0.5b"

OLLAMA_API_URL = "http://127.0.0.1:11434"

_spawned_processes = []

def _cleanup_background_processes():
    """Ensure we don't leave zombie processes (Ollama, Node) eating RAM when the plugin stops."""
    for p in _spawned_processes:
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            else:
                p.terminate()
        except Exception:
            try:
                p.terminate()
            except:
                pass

atexit.register(_cleanup_background_processes)


def is_ollama_running() -> bool:
    """Check if the local Ollama server is responding."""
    try:
        req = urllib.request.Request(f"{OLLAMA_API_URL}/api/version")
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def start_ollama():
    """Attempt to start the Ollama daemon in the background."""
    logger.info("Ollama is not running. Attempting to start it in the background...")
    try:
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            
        # On Mac/Linux, ollama serve runs the daemon
        p = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs
        )
        _spawned_processes.append(p)
        # Give it a few seconds to boot
        for _ in range(5):
            time.sleep(1)
            if is_ollama_running():
                logger.info("Successfully started Ollama.")
                return
        logger.warning("Tried to start Ollama but it is still not responding.")
    except FileNotFoundError:
        logger.error("Ollama executable not found. For a fully automatic setup, please install Ollama from https://ollama.com.")
        sys.exit(1)


def setup_and_start_gateway():
    """Silently download, install, and start the TencentDB Gateway in the background."""
    gateway_dir = os.path.expanduser("~/.jarvis-memory/tencent-gateway")
    
    # 1. Download if it doesn't exist
    if not os.path.exists(gateway_dir):
        logger.info("First run detected: Initializing Memory Database infrastructure... (This happens once)")
        try:
            subprocess.run(
                ["git", "clone", "https://github.com/TencentCloud/TencentDB-Agent-Memory.git", gateway_dir],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logger.info("Database infrastructure downloaded.")
        except FileNotFoundError:
            logger.error("'git' is required to auto-install the memory database.")
            sys.exit(1)

    # 2. Install dependencies if node_modules is missing
    if not os.path.exists(os.path.join(gateway_dir, "node_modules")):
        logger.info("Installing database dependencies...")
        try:
            subprocess.run(["npm", "install"], cwd=gateway_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            logger.error("'npm' (Node.js) is required to run the memory database.")
            sys.exit(1)

    # 3. Export configuration for GatewaySupervisor to own and auto-restart it
    os.environ["GATEWAY_START_CMD"] = "npm start"
    os.environ["GATEWAY_CWD"] = gateway_dir


def pull_model(model_name: str):
    """Tell Ollama to pull a model if it isn't already downloaded."""
    logger.info(f"Checking for local model: {model_name}...")
    try:
        # Check if it exists
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if model_name in result.stdout:
            logger.info(f"Model {model_name} is already available.")
            return

        # Pull the model
        logger.info(f"Downloading {model_name}... This may take a moment on the first run.")
        subprocess.run(["ollama", "pull", model_name], check=True)
        logger.info(f"Successfully downloaded {model_name}.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to pull model {model_name}: {e}")
    except FileNotFoundError:
        pass  # Handled in start_ollama


def configure_zero_config_env():
    """Inject the environment variables so the Gateway uses the local models."""
    logger.info("Auto-wiring environment variables for zero-config local AI...")
    
    # We tell the Gateway to point to our local Ollama instance
    os.environ.setdefault("TDAI_LLM_BASE_URL", f"{OLLAMA_API_URL}/v1")
    # Tell the Gateway which models to use
    os.environ.setdefault("TDAI_LLM_MODEL", GENERATIVE_MODEL)
    os.environ.setdefault("TDAI_EMBEDDING_MODEL", EMBEDDING_MODEL)
    # The Gateway needs some key, Ollama ignores it but Gateway might check for it
    os.environ.setdefault("TDAI_LLM_API_KEY", "ollama-local")


def main():
    """Bootstrapper entry point."""
    # Set up basic logging for the orchestrator
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    # 1. Ensure Ollama is running
    if not is_ollama_running():
        start_ollama()
        
    # 2. Ensure models are downloaded
    pull_model(EMBEDDING_MODEL)
    pull_model(GENERATIVE_MODEL)
    
    # 3. Auto-configure the environment for the memory gateway
    configure_zero_config_env()
    
    # 4. Download and start the TencentDB Gateway automatically
    setup_and_start_gateway()
    
    # 5. Start the MCP server (which connects to the Gateway)
    logger.info("Local AI infrastructure verified. Starting Jarvis Memory Server...")
    server_main()


if __name__ == "__main__":
    main()
