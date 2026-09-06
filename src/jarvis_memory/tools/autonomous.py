"""Robust, client-driven autonomous workflow state machine.

Jarvis is the workflow/state layer; Hermes remains responsible for actually
executing tools. This module is deliberately conservative about idempotency,
approval, replanning, persistence and race-group semantics.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import CONFIG
from ..workflow_store import WorkflowStore

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "cancelled"}
ACTIVE_STATUSES = {"running", "ready_to_execute", "awaiting_approval"}
VALID_RESULT_STATUSES = {"success", "failed"}


def validate_plan(plan: Dict[str, Any]) -> Optional[str]:
    if not isinstance(plan, dict):
        return "plan must be an object"
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return "plan.steps must be a non-empty list"

    seen: set[str] = set()
    valid_risks = {"low", "medium", "high"}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"step {i} must be an object"
        for key in ("id", "action", "confidence", "risk"):
            if key not in step:
                return f"step {i} missing required field '{key}'"
        if step["id"] is None or str(step["id"]) == "":
            return f"step {i} has an empty id"
        sid = str(step["id"])
        if sid in seen:
            return f"duplicate step id '{step['id']}'"
        seen.add(sid)
        if not isinstance(step["action"], str) or not step["action"].strip():
            return f"step {i} action must be a non-empty string"
        try:
            confidence = float(step["confidence"])
        except (TypeError, ValueError):
            return f"step {i} confidence must be numeric"
        if not 0.0 <= confidence <= 1.0:
            return f"step {i} confidence must be between 0 and 1"
        if str(step["risk"]).lower() not in valid_risks:
            return f"step {i} risk must be one of {sorted(valid_risks)}"
    return None


class AutonomousExecutor:
    """Persisted workflow state machine. It never executes Hermes tools itself."""

    def __init__(self, memory_engine=None, config: Optional[Dict[str, Any]] = None,
                 store: Optional[WorkflowStore] = None, planner=None):
        cfg = config or {}
        self.memory_engine = memory_engine
        self.auto_approve_confidence = float(cfg.get("auto_approve_confidence", CONFIG.auto_approve_confidence))
        self.replan_max_retries = int(cfg.get("replan_max_retries", CONFIG.replan_max_retries))
        self.step_timeout = float(cfg.get("step_timeout", CONFIG.step_timeout))
        self.store = store or WorkflowStore(CONFIG.workflow_db_path)
        self.planner = planner
        self._lock = threading.RLock()

        # Only warm-cache non-terminal workflows; SQLite remains the source of truth.
        persisted = self.store.load_all()
        self.active_workflows: Dict[str, Dict[str, Any]] = {
            wid: state for wid, state in persisted.items()
            if state.get("status") not in TERMINAL_STATUSES
        }
        if self.active_workflows:
            logger.info("Resumed %d active workflow(s) from persistent store.", len(self.active_workflows))

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _get_state(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        state = self.active_workflows.get(workflow_id)
        if state is None:
            state = self.store.load(workflow_id)
            if state is not None:
                self.active_workflows[workflow_id] = state
        return state

    def _persist(self, workflow_id: str) -> None:
        self.store.save(workflow_id, self.active_workflows[workflow_id])

    def _status_response(self, workflow_id: str) -> Dict[str, Any]:
        state = self._get_state(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}
        return {
            "workflow_id": workflow_id,
            "status": state.get("status"),
            "total_steps": len(state.get("plan", {}).get("steps", [])),
            "completed": len(set(state.get("completed_steps", []))),
            "failed": len(set(state.get("failed_steps", []))),
            "replan_count": state.get("replan_count", 0),
            "history": state.get("history", []),
        }

    def start_workflow(self, workflow_id: str, plan: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
        if not workflow_id:
            return {"error": "workflow_id is required", "status": "failed"}
        with self._lock:
            existing = self._get_state(workflow_id)
            if existing and existing.get("status") not in TERMINAL_STATUSES:
                return self.get_next_step(workflow_id)
            error = validate_plan(plan)
            if error:
                return {"error": f"Invalid plan: {error}", "status": "failed"}
            self.active_workflows[workflow_id] = {
                "plan": plan,
                "profile_id": profile_id,
                "started_at": self._now_iso(),
                "next_index": 0,
                "approved_index": None,
                "approved_step_ids": [],
                "completed_steps": [],
                "failed_steps": [],
                "status": "running",
                "history": [],
                "archived_history": [],
                "replan_count": 0,
                "replan_history": [],
                "step_started_at": None,
            }
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def _pending_indices(self, state: Dict[str, Any]) -> List[int]:
        steps = state["plan"]["steps"]
        completed = {str(x) for x in state.get("completed_steps", [])}
        cancelled = {
            str(h.get("step_id")) for h in state.get("history", [])
            if h.get("status") == "cancelled_by_race_winner"
        }
        return [i for i, step in enumerate(steps)
                if str(step.get("id")) not in completed and str(step.get("id")) not in cancelled]

    def _find_next_index(self, state: Dict[str, Any], start: int = 0) -> int:
        steps = state["plan"]["steps"]
        completed = {str(x) for x in state.get("completed_steps", [])}
        cancelled = {
            str(h.get("step_id")) for h in state.get("history", [])
            if h.get("status") == "cancelled_by_race_winner"
        }
        for i in range(max(0, start), len(steps)):
            sid = str(steps[i].get("id"))
            if sid not in completed and sid not in cancelled:
                return i
        return len(steps)

    def _race_members(self, state: Dict[str, Any], idx: int) -> List[Dict[str, Any]]:
        steps = state["plan"]["steps"]
        group = steps[idx].get("race_group_id")
        if not group:
            return [steps[idx]]
        members: List[Dict[str, Any]] = []
        completed = {str(x) for x in state.get("completed_steps", [])}
        cancelled = {
            str(h.get("step_id")) for h in state.get("history", [])
            if h.get("status") == "cancelled_by_race_winner"
        }
        for s in steps[idx:]:
            if s.get("race_group_id") != group:
                break
            sid = str(s.get("id"))
            if sid not in completed and sid not in cancelled:
                members.append(s)
        return members

    def get_next_step(self, workflow_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            state["next_index"] = self._find_next_index(state, state.get("next_index", 0))
            idx = state["next_index"]
            steps = state["plan"]["steps"]

            # Dedupe and completion are checked together so manual/automatic replans
            # can never restart a previously completed step from index 0.
            while idx < len(steps):
                step = steps[idx]
                sid = step.get("id")
                if sid in state.get("completed_steps", []):
                    idx += 1
                    state["next_index"] = idx
                    continue
                key = step.get("dedupe_key")
                if key and self.store.is_dedupe_done(key):
                    state.setdefault("history", []).append({
                        "step_id": sid,
                        "action": step.get("action"),
                        "tool": step.get("tool"),
                        "status": "skipped_duplicate",
                        "output": f"dedupe_key '{key}' already completed",
                        "error": None,
                        "reported_at": self._now_iso(),
                    })
                    if sid not in state["completed_steps"]:
                        state["completed_steps"].append(sid)
                    idx += 1
                    state["next_index"] = idx
                    continue
                break

            if idx >= len(steps):
                state["status"] = "completed" if not state.get("failed_steps") else "completed_with_failures"
                state["step_started_at"] = None
                self._persist(workflow_id)
                return self._status_response(workflow_id)

            current = steps[idx]
            members = self._race_members(state, idx)
            needs_approval = any(self._requires_approval(s) for s in members)
            approved_ids = {str(x) for x in state.get("approved_step_ids", [])}
            if needs_approval and not all(str(s.get("id")) in approved_ids for s in members):
                state["status"] = "awaiting_approval"
                state["step_started_at"] = state.get("step_started_at") or self._now_iso()
                self._persist(workflow_id)
                payload: Dict[str, Any] = {
                    "workflow_id": workflow_id,
                    "status": "awaiting_approval",
                    "requires_approval": True,
                    "approval_reason": "; ".join(sorted({self._get_approval_reason(s) for s in members if self._requires_approval(s)})),
                }
                if current.get("race_group_id"):
                    payload.update({"race_group_id": current.get("race_group_id"), "parallel_steps": members})
                else:
                    payload["step"] = current
                return payload

            state["status"] = "ready_to_execute"
            state["step_started_at"] = state.get("step_started_at") or self._now_iso()
            self._persist(workflow_id)
            if current.get("race_group_id"):
                return {
                    "workflow_id": workflow_id,
                    "status": "ready_to_execute",
                    "race_group_id": current.get("race_group_id"),
                    "parallel_steps": members,
                    "message": "Execute the listed steps in parallel. Jarvis records a logical winner; Hermes must cancel/ignore loser executions where possible.",
                    "requires_approval": False,
                }
            return {"workflow_id": workflow_id, "status": "ready_to_execute", "step": current}

    def approve_step(self, workflow_id: str, step_id: Any) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            idx = self._find_next_index(state, state.get("next_index", 0))
            if idx >= len(state["plan"]["steps"]):
                return {"error": "No pending steps to approve"}
            members = self._race_members(state, idx)
            valid = {str(s.get("id")) for s in members}
            if str(step_id) not in valid:
                return {"error": f"Step {step_id} is not currently pending"}
            state.setdefault("approved_step_ids", []).append(step_id)
            state["approved_step_ids"] = list(dict.fromkeys(state["approved_step_ids"]))
            state["approved_index"] = idx
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def report_step_result(self, workflow_id: str, step_id: Any, status: str,
                           output: Optional[str] = None, error: Optional[str] = None) -> Dict[str, Any]:
        if status not in VALID_RESULT_STATUSES:
            return {"error": f"Unsupported status '{status}'"}
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            steps = state["plan"]["steps"]

            # Idempotency: if the same execution report arrives again, return the
            # current state instead of appending duplicate history or advancing twice.
            for record in reversed(state.get("history", [])):
                if record.get("step_id") == step_id and record.get("status") == status:
                    return self.get_next_step(workflow_id)
                if record.get("step_id") == step_id and record.get("status") == "cancelled_by_race_winner":
                    return {"workflow_id": workflow_id, "status": "ignored_loser_result", "step_id": step_id}

            idx = self._find_next_index(state, state.get("next_index", 0))
            if idx >= len(steps):
                return self._status_response(workflow_id)
            current = steps[idx]
            group = current.get("race_group_id")
            valid_step: Optional[Dict[str, Any]] = None
            if group:
                for s in self._race_members(state, idx):
                    if str(s.get("id")) == str(step_id):
                        valid_step = s
                        break
            elif str(current.get("id")) == str(step_id):
                valid_step = current
            if valid_step is None:
                return {"error": f"Step {step_id} is not currently expected"}

            record = {
                "step_id": valid_step.get("id"),
                "action": valid_step.get("action"),
                "tool": valid_step.get("tool"),
                "status": status,
                "output": output,
                "error": error,
                "reported_at": self._now_iso(),
            }
            state.setdefault("history", []).append(record)

            if status == "success":
                sid = valid_step.get("id")
                if sid not in state["completed_steps"]:
                    state["completed_steps"].append(sid)
                if valid_step.get("dedupe_key"):
                    self.store.mark_dedupe_done(valid_step["dedupe_key"], workflow_id, sid)
                repair_match = re.search(r"TOOL REPAIR:.*?['\"]([^'\"]+)['\"]", valid_step.get("action", ""), re.I)
                if repair_match:
                    self.store.mark_tool_fixed(repair_match.group(1))

                if group:
                    for loser in self._race_members(state, idx):
                        loser_id = loser.get("id")
                        if str(loser_id) != str(step_id):
                            state.setdefault("history", []).append({
                                "step_id": loser_id,
                                "action": loser.get("action"),
                                "tool": loser.get("tool"),
                                "status": "cancelled_by_race_winner",
                                "output": f"Logical cancellation because step {step_id} won the race.",
                                "error": None,
                                "reported_at": self._now_iso(),
                            })
                    state["next_index"] = self._find_next_index(state, idx + 1)
                else:
                    state["next_index"] = self._find_next_index(state, idx + 1)
            else:
                sid = valid_step.get("id")
                if sid not in state["failed_steps"]:
                    state["failed_steps"].append(sid)
                if group:
                    remaining = self._race_members(state, idx)
                    # Wait for all racers to report before deciding the group failed.
                    unresolved = [s for s in remaining if not any(
                        h.get("step_id") == s.get("id") and h.get("status") in VALID_RESULT_STATUSES
                        for h in state.get("history", [])
                    )]
                    if unresolved:
                        self._persist(workflow_id)
                        return self._status_response(workflow_id)
                    state["next_index"] = self._find_next_index(state, idx + 1)
                else:
                    state["next_index"] = self._find_next_index(state, idx + 1)

            state["approved_step_ids"] = []
            state["approved_index"] = None
            state["step_started_at"] = None
            self._persist(workflow_id)

            if self.memory_engine:
                try:
                    self.memory_engine.add_memory(
                        state["profile_id"],
                        f"Workflow {workflow_id} step {step_id} ({valid_step.get('action')}): {status}" + (f" — {error}" if error else ""),
                        {"type": "execution_log" if status == "success" else "error_log", "workflow_id": workflow_id},
                    )
                except Exception:
                    logger.debug("Memory logging failed for workflow %s", workflow_id, exc_info=True)

            if status == "failed" and self.planner and state.get("replan_count", 0) < self.replan_max_retries:
                return self._auto_replan(workflow_id, valid_step, error or "Unknown error")
            if status == "failed" and not self.planner:
                state["status"] = "completed_with_failures" if state.get("completed_steps") else "failed"
                self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def _auto_replan(self, workflow_id: str, failed_step: Dict[str, Any], error: str) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            state["replan_count"] = state.get("replan_count", 0) + 1
            try:
                new_plan = self.planner.replan(
                    original_plan=state["plan"],
                    failed_step=failed_step.get("id"),
                    error=error,
                    history=state.get("history", []),
                )
            except Exception as exc:
                logger.exception("Auto-replan failed")
                state.setdefault("replan_history", []).append({
                    "attempt": state["replan_count"],
                    "failed_step_id": failed_step.get("id"),
                    "error": error,
                    "result": "planner_exception",
                    "detail": str(exc),
                    "at": self._now_iso(),
                })
                state["status"] = "completed_with_failures" if state.get("completed_steps") else "failed"
                self._persist(workflow_id)
                return self._status_response(workflow_id)

            plan_error = validate_plan(new_plan)
            if plan_error:
                state.setdefault("replan_history", []).append({
                    "attempt": state["replan_count"],
                    "failed_step_id": failed_step.get("id"),
                    "error": error,
                    "result": "invalid_plan",
                    "detail": plan_error,
                    "at": self._now_iso(),
                })
                self._persist(workflow_id)
                return self._status_response(workflow_id)

            old_history = state.get("history", [])
            state["archived_history"] = state.get("archived_history", []) + old_history
            state["history"] = []
            state["plan"] = new_plan
            state["failed_steps"] = []
            state["approved_step_ids"] = []
            state["approved_index"] = None
            state["status"] = "running"
            # Crucial: never default to 0. Start at the first new, incomplete step.
            state["next_index"] = self._find_next_index(state, 0)
            state.setdefault("replan_history", []).append({
                "attempt": state["replan_count"],
                "failed_step_id": failed_step.get("id"),
                "error": error,
                "result": "replanned",
                "new_steps_count": len(new_plan["steps"]),
                "replan_reason": new_plan.get("replan_reason", ""),
                "at": self._now_iso(),
            })
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            if state.get("status") in TERMINAL_STATUSES:
                return {"error": f"Workflow already in terminal state: {state['status']}"}
            state["status"] = "cancelled"
            state["step_started_at"] = None
            state.setdefault("history", []).append({
                "step_id": None,
                "action": "workflow_cancelled",
                "tool": None,
                "status": "cancelled",
                "output": reason or "Cancelled by user/system",
                "error": None,
                "reported_at": self._now_iso(),
            })
            self._persist(workflow_id)
            return {
                "workflow_id": workflow_id,
                "status": "cancelled",
                "reason": reason or "Cancelled by user/system",
                "completed_steps": len(set(state.get("completed_steps", []))),
                "total_steps": len(state.get("plan", {}).get("steps", [])),
            }

    def check_stalled_workflows(self, timeout_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        timeout = self.step_timeout if timeout_seconds is None else float(timeout_seconds)
        now = datetime.now(timezone.utc)
        stalled: List[Dict[str, Any]] = []
        with self._lock:
            workflows = dict(self.active_workflows)
        for wf_id, state in workflows.items():
            if state.get("status") not in {"running", "ready_to_execute"}:
                continue
            started = state.get("step_started_at")
            if not started:
                continue
            try:
                started_dt = datetime.fromisoformat(started)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                elapsed = (now - started_dt).total_seconds()
            except (ValueError, TypeError):
                continue
            if elapsed > timeout:
                idx = self._find_next_index(state, state.get("next_index", 0))
                step = state.get("plan", {}).get("steps", [])[idx] if idx < len(state.get("plan", {}).get("steps", [])) else None
                stalled.append({
                    "workflow_id": wf_id,
                    "profile_id": state.get("profile_id"),
                    "goal": state.get("plan", {}).get("goal", ""),
                    "stalled_step": step,
                    "elapsed_seconds": round(elapsed, 1),
                    "step_started_at": started,
                })
        return stalled

    def get_workflow_status(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._get_state(workflow_id)

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        return self.store.list_by_status("awaiting_approval")

    def _requires_approval(self, step: Dict[str, Any]) -> bool:
        if "requires_approval" in step:
            return bool(step["requires_approval"])
        if str(step.get("risk", "")).lower() == "high":
            return True
        try:
            return float(step.get("confidence", 0.0)) < self.auto_approve_confidence
        except (TypeError, ValueError):
            return True

    def _get_approval_reason(self, step: Dict[str, Any]) -> str:
        reasons: List[str] = []
        if str(step.get("risk", "")).lower() == "high":
            reasons.append("High risk operation")
        try:
            if float(step.get("confidence", 0.0)) < self.auto_approve_confidence:
                reasons.append(f"Low confidence ({float(step.get('confidence', 0.0)):.0%})")
        except (TypeError, ValueError):
            reasons.append("Invalid confidence")
        if step.get("requires_approval"):
            reasons.append("Explicitly marked for approval")
        return "; ".join(reasons) or "Approval required"

    def reflect(self, workflow_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            history = state.get("archived_history", []) + state.get("history", [])
            successes = [h for h in history if h.get("status") == "success"]
            failures = [h for h in history if h.get("status") == "failed"]
            successful_tools = sorted({h.get("tool") for h in successes if h.get("tool")})
            failed_tools = sorted({h.get("tool") for h in failures if h.get("tool")})
            attempts = len(successes) + len(failures)
            success_rate = len(successes) / max(1, attempts)
            goal = state.get("plan", {}).get("goal", "")
            lesson = (
                f"WORKFLOW REFLECTION: Goal '{goal}' finished with status {state.get('status')}. "
                f"Success rate: {success_rate:.0%}. "
                f"Worked tools: {', '.join(successful_tools) or 'none'}. "
                f"Failed tools: {', '.join(failed_tools) or 'none'}. "
                f"Replans: {state.get('replan_count', 0)}."
            )
            captured = None
            if self.memory_engine:
                try:
                    captured = self.memory_engine.add_memory(
                        state["profile_id"], lesson,
                        {"type": "workflow_reflection", "workflow_id": workflow_id,
                         "success_rate": round(success_rate, 2), "replan_count": state.get("replan_count", 0)},
                    )
                except Exception:
                    logger.debug("Reflection memory write failed", exc_info=True)
            return {
                "workflow_id": workflow_id,
                "lesson": lesson,
                "patterns": {
                    "success_rate": round(success_rate, 2),
                    "successful_tools": successful_tools,
                    "failed_tools": failed_tools,
                    "replan_count": state.get("replan_count", 0),
                },
                "captured": captured,
            }

    def self_evolve(self, profile_id: str) -> Dict[str, Any]:
        all_states = self.store.load_all()
        profile = {
            wid: state for wid, state in all_states.items()
            if state.get("profile_id") == profile_id and state.get("status") in TERMINAL_STATUSES
        }
        if not profile:
            return {"profile_id": profile_id, "total_workflows": 0,
                    "message": "No completed workflows found for this profile."}
        total = len(profile)
        fully_completed = sum(1 for s in profile.values() if s.get("status") == "completed")
        tool_successes: Dict[str, int] = {}
        tool_failures: Dict[str, int] = {}
        total_replans = sum(s.get("replan_count", 0) for s in profile.values())
        for state in profile.values():
            for h in state.get("archived_history", []) + state.get("history", []):
                tool = h.get("tool") or "unknown"
                if h.get("status") == "success":
                    tool_successes[tool] = tool_successes.get(tool, 0) + 1
                elif h.get("status") == "failed":
                    tool_failures[tool] = tool_failures.get(tool, 0) + 1
        unreliable = sorted(tool_failures, key=tool_failures.get, reverse=True)
        reliable = sorted([t for t, n in tool_successes.items() if n >= 2 and tool_failures.get(t, 0) == 0],
                          key=tool_successes.get, reverse=True)
        for tool in unreliable:
            fails = tool_failures[tool]
            successes = tool_successes.get(tool, 0)
            if fails >= 3 and successes / max(1, successes + fails) < 0.2:
                self.store.mark_tool_broken(tool, f"Failed {fails} times; auto-flagged by self-evolution")
        return {
            "profile_id": profile_id,
            "total_workflows": total,
            "completion_rate": round(fully_completed / total, 2),
            "total_replans": total_replans,
            "reliable_tools": reliable,
            "unreliable_tools": unreliable,
            "tool_success_counts": tool_successes,
            "tool_failure_counts": tool_failures,
        }
