"""
Single place for every knob that was previously a hardcoded constant
scattered across core.py / server.py / gateway_supervisor.py / tencent_memory.py.
Production deployments need to tune these per-environment without editing
source — everything here reads from an env var with the same default the
code previously hardcoded, so behavior is unchanged until you set something.
"""

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    # MemoryCore Gateway connection
    gateway_url: str = field(default_factory=lambda: os.environ.get("TDAI_GATEWAY_URL", "http://127.0.0.1:8420"))
    gateway_api_key: str = field(default_factory=lambda: os.environ.get("TDAI_API_KEY", ""))
    gateway_start_cmd: str = field(default_factory=lambda: os.environ.get("GATEWAY_START_CMD", ""))

    # Circuit breaker (TencentMemoryClient)
    circuit_failure_threshold: int = field(default_factory=lambda: _i("TDAI_CIRCUIT_FAILURE_THRESHOLD", 5))
    circuit_cooldown_seconds: float = field(default_factory=lambda: _f("TDAI_CIRCUIT_COOLDOWN", 60))

    # Gateway supervisor
    watchdog_interval: float = field(default_factory=lambda: _f("JARVIS_WATCHDOG_INTERVAL", 10))
    watchdog_failure_threshold: int = field(default_factory=lambda: _i("JARVIS_WATCHDOG_FAILURE_THRESHOLD", 3))
    sync_interval: float = field(default_factory=lambda: _f("JARVIS_SYNC_INTERVAL", 300))

    # Workflow engine
    auto_approve_confidence: float = field(default_factory=lambda: _f("JARVIS_AUTO_APPROVE_CONFIDENCE", 0.85))
    workflow_db_path: str = field(default_factory=lambda: os.environ.get("JARVIS_WORKFLOW_DB", "~/.jarvis-memory/workflows.db"))

    # Planner LLM (optional — leave unset to use the heuristic fallback)
    planner_llm_url: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_URL", ""))
    planner_llm_key: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_KEY", ""))
    planner_llm_model: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_MODEL", ""))

    # Auto-replan on step failure (cap to avoid infinite loops)
    replan_max_retries: int = field(default_factory=lambda: _i("JARVIS_REPLAN_MAX_RETRIES", 3))

    # Step timeout for stall detection (seconds)
    step_timeout: float = field(default_factory=lambda: _f("JARVIS_STEP_TIMEOUT", 1800))

    # Profile TTL for sync eviction (seconds of inactivity before dropping)
    profile_ttl: float = field(default_factory=lambda: _f("JARVIS_PROFILE_TTL", 3600))


CONFIG = Config()
