"""Central runtime configuration with safe, environment-driven defaults."""

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    gateway_url: str = field(default_factory=lambda: os.environ.get("TDAI_GATEWAY_URL", "http://127.0.0.1:8420"))
    gateway_api_key: str = field(default_factory=lambda: os.environ.get("TDAI_GATEWAY_API_KEY") or os.environ.get("TDAI_API_KEY", ""))
    gateway_service_id: str = field(default_factory=lambda: os.environ.get("TDAI_GATEWAY_SERVICE_ID") or os.environ.get("TDAI_SERVICE_ID", "default"))
    gateway_team_id: str = field(default_factory=lambda: os.environ.get("TDAI_TEAM_ID", "default"))
    gateway_agent_id: str = field(default_factory=lambda: os.environ.get("TDAI_AGENT_ID", "default"))
    gateway_api_version: str = field(default_factory=lambda: os.environ.get("TDAI_API_VERSION", "v3"))
    gateway_start_cmd: str = field(default_factory=lambda: os.environ.get("GATEWAY_START_CMD", ""))
    gateway_cwd: str = field(default_factory=lambda: os.environ.get("GATEWAY_CWD", ""))

    circuit_failure_threshold: int = field(default_factory=lambda: max(1, _i("TDAI_CIRCUIT_FAILURE_THRESHOLD", 5)))
    circuit_cooldown_seconds: float = field(default_factory=lambda: max(1.0, _f("TDAI_CIRCUIT_COOLDOWN", 60)))

    watchdog_interval: float = field(default_factory=lambda: max(1.0, _f("JARVIS_WATCHDOG_INTERVAL", 10)))
    watchdog_failure_threshold: int = field(default_factory=lambda: max(1, _i("JARVIS_WATCHDOG_FAILURE_THRESHOLD", 3)))
    sync_interval: float = field(default_factory=lambda: max(10.0, _f("JARVIS_SYNC_INTERVAL", 300)))
    profile_ttl: float = field(default_factory=lambda: max(60.0, _f("JARVIS_PROFILE_TTL", 3600)))
    workflow_retention_days: int = field(default_factory=lambda: max(1, _i("JARVIS_WORKFLOW_RETENTION_DAYS", 30)))

    auto_approve_confidence: float = field(default_factory=lambda: min(1.0, max(0.0, _f("JARVIS_AUTO_APPROVE_CONFIDENCE", 0.85))))
    workflow_db_path: str = field(default_factory=lambda: os.environ.get("JARVIS_WORKFLOW_DB", "~/.jarvis-memory/workflows.db"))
    replan_max_retries: int = field(default_factory=lambda: _i("JARVIS_REPLAN_MAX_RETRIES", 3))
    step_timeout: float = field(default_factory=lambda: max(1.0, _f("JARVIS_STEP_TIMEOUT", 1800)))

    # Optional OpenAI-compatible planner endpoint; unset means deterministic fallback.
    planner_llm_url: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_URL", ""))
    planner_llm_key: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_KEY", ""))
    planner_llm_model: str = field(default_factory=lambda: os.environ.get("JARVIS_PLANNER_LLM_MODEL", ""))


CONFIG = Config()
