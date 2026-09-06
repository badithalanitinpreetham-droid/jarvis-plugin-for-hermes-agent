"""Shared validation for the Jarvis ↔ Hermes workflow contract."""
from __future__ import annotations

from typing import Any, Dict, Optional


VALID_RISKS = {"low", "medium", "high"}
VALID_EXECUTION_MODES = {"sequential", "parallel", "race"}


def validate_plan(plan: Dict[str, Any]) -> Optional[str]:
    """Validate step IDs, dependencies and execution semantics before persistence."""
    if not isinstance(plan, dict):
        return "plan must be an object"
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return "plan.steps must be a non-empty list"

    ids: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"step {index} must be an object"
        for key in ("id", "action", "confidence", "risk"):
            if key not in step:
                return f"step {index} missing required field '{key}'"

        sid = str(step.get("id"))
        if not sid:
            return f"step {index} has an empty id"
        if sid in ids:
            return f"duplicate step id '{sid}'"
        ids.add(sid)

        if not isinstance(step.get("action"), str) or not step["action"].strip():
            return f"step {index} action must be a non-empty string"
        try:
            confidence = float(step.get("confidence"))
        except (TypeError, ValueError):
            return f"step {index} confidence must be numeric"
        if not 0.0 <= confidence <= 1.0:
            return f"step {index} confidence must be between 0 and 1"
        if str(step.get("risk", "")).lower() not in VALID_RISKS:
            return f"step {index} risk must be one of {sorted(VALID_RISKS)}"

        dependencies = step.get("depends_on", []) or []
        if not isinstance(dependencies, list):
            return f"step {index} depends_on must be a list"
        for dependency in dependencies:
            dep = str(dependency)
            if dep == sid:
                return f"step {index} cannot depend on itself"

        mode = str(step.get("execution_mode", "sequential"))
        if mode not in VALID_EXECUTION_MODES:
            return f"step {index} execution_mode must be sequential/parallel/race"
        if mode == "race" and not step.get("race_group_id"):
            return f"step {index} race execution requires race_group_id"
        if mode == "parallel" and step.get("race_group_id"):
            return f"step {index} cannot mix parallel execution with race_group_id"

    known = ids
    graph: Dict[str, list[str]] = {}
    for index, step in enumerate(steps):
        sid = str(step["id"])
        deps = [str(dep) for dep in (step.get("depends_on", []) or [])]
        unknown = [dep for dep in deps if dep not in known]
        if unknown:
            return f"step {index} depends on unknown step '{unknown[0]}'"
        graph[sid] = deps

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        for dep in graph[node]:
            if not visit(dep):
                return False
        visiting.remove(node)
        visited.add(node)
        return True

    if any(not visit(node) for node in graph):
        return "plan contains a dependency cycle"
    return None
