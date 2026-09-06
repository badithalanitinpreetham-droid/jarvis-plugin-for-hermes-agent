"""Durable, client-driven workflow state machine for Hermes.

Jarvis owns workflow state, dependencies, approvals, deduplication and recovery.
Hermes remains responsible for executing real tools and workers.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import CONFIG
from ..orchestration.contracts import validate_plan
from ..workflow_store import WorkflowStore

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "cancelled"}
ACTIVE_STATUSES = {"running", "ready_to_execute", "awaiting_approval"}
VALID_RESULT_STATUSES = {"success", "failed"}


class AutonomousExecutor:
    """Persistent workflow state machine. It never executes Hermes tools itself."""

    def __init__(self, memory_engine=None, config: Optional[Dict[str, Any]] = None,
                 store: Optional[WorkflowStore] = None, planner=None):
        cfg = config or {}
        self.memory_engine = memory_engine
        self.auto_approve_confidence = min(1.0, max(0.0, float(cfg.get("auto_approve_confidence", CONFIG.auto_approve_confidence))))
        self.replan_max_retries = max(0, int(cfg.get("replan_max_retries", CONFIG.replan_max_retries)))
        self.step_timeout = max(1.0, float(cfg.get("step_timeout", CONFIG.step_timeout)))
        self.store = store or WorkflowStore(CONFIG.workflow_db_path)
        self.planner = planner
        self._lock = threading.RLock()
        persisted = self.store.load_all()
        self.active_workflows = {wid: state for wid, state in persisted.items() if state.get("status") not in TERMINAL_STATUSES}

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
        state = self.active_workflows.get(workflow_id)
        if state is not None:
            self.store.save(workflow_id, state)

    @staticmethod
    def _completed_ids(state: Dict[str, Any]) -> set[str]:
        return {str(x) for x in state.get("completed_steps", [])}

    @staticmethod
    def _cancelled_ids(state: Dict[str, Any]) -> set[str]:
        return {str(h.get("step_id")) for h in state.get("history", []) if h.get("status") in {"cancelled_by_race_winner", "skipped_duplicate"}}

    def _terminal_ids(self, state: Dict[str, Any]) -> set[str]:
        return self._completed_ids(state) | self._cancelled_ids(state)

    def _dependencies_satisfied(self, state: Dict[str, Any], step: Dict[str, Any]) -> bool:
        terminal = self._terminal_ids(state)
        return all(str(dep) in terminal for dep in (step.get("depends_on", []) or []))

    def _pending_indices(self, state: Dict[str, Any]) -> List[int]:
        terminal = self._terminal_ids(state)
        return [i for i, step in enumerate(state.get("plan", {}).get("steps", [])) if str(step.get("id")) not in terminal]

    def _ready_indices(self, state: Dict[str, Any]) -> List[int]:
        terminal = self._terminal_ids(state)
        return [i for i, step in enumerate(state.get("plan", {}).get("steps", []))
                if str(step.get("id")) not in terminal and self._dependencies_satisfied(state, step)]

    def _find_next_index(self, state: Dict[str, Any], start: int = 0) -> int:
        ready = [i for i in self._ready_indices(state) if i >= max(0, start)]
        return min(ready) if ready else len(state.get("plan", {}).get("steps", []))

    def _race_members(self, state: Dict[str, Any], idx: int) -> List[Dict[str, Any]]:
        steps = state.get("plan", {}).get("steps", [])
        if idx >= len(steps):
            return []
        group = steps[idx].get("race_group_id")
        if not group:
            return [steps[idx]]
        return [step for step in steps if step.get("race_group_id") == group and str(step.get("id")) not in self._terminal_ids(state) and self._dependencies_satisfied(state, step)]

    def _parallel_members(self, state: Dict[str, Any], idx: int) -> List[Dict[str, Any]]:
        steps = state["plan"]["steps"]
        current = steps[idx]
        if current.get("execution_mode", "sequential") != "parallel":
            return [current]
        group = current.get("parallel_group_id")
        ready = self._ready_indices(state)
        if group:
            return [steps[i] for i in ready if steps[i].get("execution_mode") == "parallel" and steps[i].get("parallel_group_id") == group]
        return [steps[i] for i in ready if steps[i].get("execution_mode") == "parallel"]

    def _active_members(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        ids = {str(x) for x in state.get("active_batch", [])}
        return [step for step in state.get("plan", {}).get("steps", []) if str(step.get("id")) in ids]

    def _status_response(self, workflow_id: str) -> Dict[str, Any]:
        state = self._get_state(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}
        return {"workflow_id": workflow_id, "status": state.get("status"),
                "total_steps": len(state.get("plan", {}).get("steps", [])),
                "completed": len(self._completed_ids(state)),
                "failed": len({str(x) for x in state.get("failed_steps", [])}),
                "replan_count": state.get("replan_count", 0), "history": list(state.get("history", []))}

    def start_workflow(self, workflow_id: str, plan: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
        workflow_id, profile_id = str(workflow_id or "").strip(), str(profile_id or "").strip()
        if not workflow_id:
            return {"error": "workflow_id is required", "status": "failed"}
        if not profile_id:
            return {"error": "profile_id is required", "status": "failed"}
        with self._lock:
            existing = self._get_state(workflow_id)
            if existing and existing.get("status") not in TERMINAL_STATUSES:
                return self.get_next_step(workflow_id)
            error = validate_plan(plan)
            if error:
                return {"error": f"Invalid plan: {error}", "status": "failed"}
            self.active_workflows[workflow_id] = {"plan": plan, "profile_id": profile_id, "started_at": self._now_iso(),
                "next_index": 0, "approved_index": None, "approved_step_ids": [], "completed_steps": [],
                "failed_steps": [], "active_batch": [], "status": "running", "history": [], "archived_history": [],
                "replan_count": 0, "replan_history": [], "step_started_at": None}
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def _normalise_duplicate_steps(self, state: Dict[str, Any]) -> None:
        completed = self._completed_ids(state)
        for step in state.get("plan", {}).get("steps", []):
            sid, key = step.get("id"), step.get("dedupe_key")
            if str(sid) in completed or not key or not self.store.is_dedupe_done(str(key)):
                continue
            state.setdefault("history", []).append({"step_id": sid, "action": step.get("action"), "tool": step.get("tool"),
                "status": "skipped_duplicate", "output": f"dedupe_key '{key}' already completed", "error": None,
                "reported_at": self._now_iso()})
            state.setdefault("completed_steps", []).append(sid)
            completed.add(str(sid))

    def get_next_step(self, workflow_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            self._normalise_duplicate_steps(state)
            steps = state.get("plan", {}).get("steps", [])
            active = self._active_members(state)
            if active:
                members = [s for s in active if str(s.get("id")) not in self._terminal_ids(state)]
                if members:
                    state["status"] = "ready_to_execute"
                    self._persist(workflow_id)
                    return self._execution_payload(workflow_id, members)
                state["active_batch"] = []
            ready = self._ready_indices(state)
            if not ready:
                if not self._pending_indices(state):
                    state["status"] = "completed" if not state.get("failed_steps") else "completed_with_failures"
                    state["step_started_at"] = None
                    state["next_index"] = len(steps)
                    self._persist(workflow_id)
                    return self._status_response(workflow_id)
                state["status"] = "blocked"
                self._persist(workflow_id)
                return {"workflow_id": workflow_id, "status": "blocked", "message": "No pending step has its dependencies satisfied."}
            idx, current = ready[0], steps[ready[0]]
            if current.get("execution_mode") == "race":
                members = self._race_members(state, idx)
            elif current.get("execution_mode") == "parallel":
                members = self._parallel_members(state, idx)
            else:
                members = [current]
            approved = {str(x) for x in state.get("approved_step_ids", [])}
            missing = [s for s in members if str(s.get("id")) not in approved]
            if any(self._requires_approval(s) for s in members) and missing:
                state["active_batch"] = [s.get("id") for s in members]
                state["status"] = "awaiting_approval"
                state["step_started_at"] = state.get("step_started_at") or self._now_iso()
                self._persist(workflow_id)
                payload = {"workflow_id": workflow_id, "status": "awaiting_approval", "requires_approval": True,
                    "approval_reason": "; ".join(sorted({self._get_approval_reason(s) for s in missing}))}
                if len(members) > 1:
                    payload["parallel_steps"] = members
                else:
                    payload["step"] = current
                return payload
            state["active_batch"] = [s.get("id") for s in members] if len(members) > 1 else []
            state["next_index"] = idx
            state["status"] = "ready_to_execute"
            state["step_started_at"] = state.get("step_started_at") or self._now_iso()
            self._persist(workflow_id)
            return self._execution_payload(workflow_id, members)

    def _execution_payload(self, workflow_id: str, members: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {"workflow_id": workflow_id, "status": "ready_to_execute", "requires_approval": False}
        if len(members) > 1:
            payload["parallel_steps"] = members
            if members[0].get("execution_mode") == "race":
                payload["race_group_id"] = members[0].get("race_group_id")
                payload["message"] = "Execute alternatives; report the first successful result as the race winner."
            else:
                payload["message"] = "Execute the listed independent steps in parallel and report each result."
        else:
            payload["step"] = members[0]
        return payload

    def approve_step(self, workflow_id: str, step_id: Any) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            active = self._active_members(state)
            if not active:
                ready = self._ready_indices(state)
                if not ready:
                    return {"error": "No pending steps to approve"}
                idx, step = ready[0], state["plan"]["steps"][ready[0]]
                active = self._race_members(state, idx) if step.get("execution_mode") == "race" else self._parallel_members(state, idx) if step.get("execution_mode") == "parallel" else [step]
            if str(step_id) not in {str(s.get("id")) for s in active}:
                return {"error": f"Step {step_id} is not currently pending"}
            state.setdefault("approved_step_ids", []).append(step_id)
            if len(active) > 1:
                state["approved_step_ids"] += [s.get("id") for s in active]
            state["approved_step_ids"] = list(dict.fromkeys(state["approved_step_ids"]))
            state["approved_index"] = state.get("next_index")
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def report_step_result(self, workflow_id: str, step_id: Any, status: str, output: Optional[str] = None, error: Optional[str] = None) -> Dict[str, Any]:
        if status not in VALID_RESULT_STATUSES:
            return {"error": f"Unsupported status '{status}'"}
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            active = self._active_members(state)
            if not active:
                ready = self._ready_indices(state)
                if not ready:
                    return self._status_response(workflow_id)
                idx = ready[0]
                current = state["plan"]["steps"][idx]
                active = self._race_members(state, idx) if current.get("execution_mode") == "race" else self._parallel_members(state, idx) if current.get("execution_mode") == "parallel" else [current]
            target = next((s for s in active if str(s.get("id")) == str(step_id)), None)
            if target is None:
                if any(str(h.get("step_id")) == str(step_id) for h in state.get("history", [])):
                    return {"error": f"Step {step_id} has already been reported"}
                return {"error": f"Step {step_id} is not currently expected"}
            if any(str(h.get("step_id")) == str(step_id) and h.get("status") in VALID_RESULT_STATUSES for h in state.get("history", [])):
                return {"error": f"Step {step_id} has already been reported"}
            state.setdefault("history", []).append({"step_id": target.get("id"), "action": target.get("action"), "tool": target.get("tool"), "status": status, "output": output, "error": error, "reported_at": self._now_iso()})
            sid = target.get("id")
            is_race = target.get("execution_mode") == "race" and bool(target.get("race_group_id"))
            if status == "success":
                if sid not in state.setdefault("completed_steps", []):
                    state["completed_steps"].append(sid)
                if target.get("dedupe_key"):
                    self.store.mark_dedupe_done(str(target["dedupe_key"]), workflow_id, sid)
                if is_race:
                    for loser in active:
                        loser_id = loser.get("id")
                        if str(loser_id) != str(step_id) and loser_id not in state.get("completed_steps", []):
                            state.setdefault("history", []).append({"step_id": loser_id, "action": loser.get("action"), "tool": loser.get("tool"), "status": "cancelled_by_race_winner", "output": f"Logical cancellation because step {step_id} won the race.", "error": None, "reported_at": self._now_iso()})
            elif sid not in state.setdefault("failed_steps", []):
                state["failed_steps"].append(sid)

            if len(active) > 1 and not is_race:
                reported = {str(h.get("step_id")): h.get("status") for h in state.get("history", []) if str(h.get("step_id")) in {str(s.get("id")) for s in active}}
                finished = all(str(s.get("id")) in self._completed_ids(state) or reported.get(str(s.get("id"))) == "failed" for s in active)
                if not finished:
                    state["status"] = "running"
                    self._persist(workflow_id)
                    self._record_memory(state, target, status, error)
                    return {"workflow_id": workflow_id, "status": "running", "parallel": True, "pending_step_ids": [s.get("id") for s in active if str(s.get("id")) not in self._terminal_ids(state)]}
            state["active_batch"] = []
            state["approved_step_ids"] = []
            state["approved_index"] = None
            state["step_started_at"] = None
            state["next_index"] = self._find_next_index(state, 0)
            self._persist(workflow_id)
            self._record_memory(state, target, status, error)
            if status == "failed" and not is_race and self.planner and state.get("replan_count", 0) < self.replan_max_retries:
                return self._auto_replan(workflow_id, target, error or "Unknown error")
            if status == "failed" and not is_race and not self.planner:
                state["status"] = "completed_with_failures" if state.get("completed_steps") else "failed"
                self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def _record_memory(self, state: Dict[str, Any], step: Dict[str, Any], status: str, error: Optional[str]) -> None:
        if not self.memory_engine:
            return
        try:
            text = f"Workflow {state.get('plan', {}).get('goal', '')} step {step.get('id')} ({step.get('action')}): {status}"
            if error:
                text += f" — {error}"
            self.memory_engine.add_memory(state["profile_id"], text, {"type": "execution_log" if status == "success" else "error_log", "workflow_id": state.get("plan", {}).get("goal", "")})
        except Exception:
            logger.debug("Memory logging failed", exc_info=True)

    def _auto_replan(self, workflow_id: str, failed_step: Dict[str, Any], error: str) -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            state["replan_count"] = state.get("replan_count", 0) + 1
            try:
                new_plan = self.planner.replan(state["plan"], failed_step.get("id"), error, state.get("history", []))
            except Exception as exc:
                state.setdefault("replan_history", []).append({"attempt": state["replan_count"], "failed_step_id": failed_step.get("id"), "error": error, "result": "planner_exception", "detail": str(exc), "at": self._now_iso()})
                state["status"] = "completed_with_failures" if state.get("completed_steps") else "failed"
                self._persist(workflow_id)
                return self._status_response(workflow_id)
            plan_error = validate_plan(new_plan)
            if plan_error:
                state.setdefault("replan_history", []).append({"attempt": state["replan_count"], "failed_step_id": failed_step.get("id"), "error": error, "result": "invalid_plan", "detail": plan_error, "at": self._now_iso()})
                state["status"] = "completed_with_failures" if state.get("completed_steps") else "failed"
                self._persist(workflow_id)
                return self._status_response(workflow_id)
            state["archived_history"] = state.get("archived_history", []) + state.get("history", [])
            state["history"] = []
            state["plan"] = new_plan
            state["failed_steps"] = []
            state["approved_step_ids"] = []
            state["active_batch"] = []
            state["approved_index"] = None
            state["next_index"] = 0
            state["status"] = "running"
            state.setdefault("replan_history", []).append({"attempt": state["replan_count"], "failed_step_id": failed_step.get("id"), "error": error, "result": "replanned", "new_steps_count": len(new_plan.get("steps", [])), "replan_reason": new_plan.get("replan_reason", ""), "at": self._now_iso()})
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            state = self._get_state(workflow_id)
            if state is None:
                return {"error": "Workflow not found"}
            if state.get("status") in TERMINAL_STATUSES:
                return {"error": f"Workflow already in terminal state: {state.get('status')}"}
            state["status"] = "cancelled"
            state["active_batch"] = []
            state["step_started_at"] = None
            state.setdefault("history", []).append({"step_id": None, "action": "workflow_cancelled", "tool": None, "status": "cancelled", "output": reason or "Cancelled by user/system", "error": None, "reported_at": self._now_iso()})
            self._persist(workflow_id)
            return {"workflow_id": workflow_id, "status": "cancelled", "reason": reason or "Cancelled by user/system", "completed_steps": len(self._completed_ids(state)), "total_steps": len(state.get("plan", {}).get("steps", []))}

    def check_stalled_workflows(self, timeout_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        timeout = self.step_timeout if timeout_seconds is None else max(1.0, float(timeout_seconds))
        now = datetime.now(timezone.utc)
        stalled = []
        with self._lock:
            workflows = dict(self.active_workflows)
        for wf_id, state in workflows.items():
            if state.get("status") not in ACTIVE_STATUSES:
                continue
            started = state.get("step_started_at")
            if not started:
                continue
            try:
                dt = datetime.fromisoformat(started)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                elapsed = (now - dt).total_seconds()
            except (TypeError, ValueError):
                continue
            if elapsed > timeout:
                active = self._active_members(state)
                step = active[0] if active else None
                if step is None:
                    ready = self._ready_indices(state)
                    steps = state.get("plan", {}).get("steps", [])
                    step = steps[ready[0]] if ready else None
                stalled.append({"workflow_id": wf_id, "profile_id": state.get("profile_id"), "goal": state.get("plan", {}).get("goal", ""), "stalled_step": step, "elapsed_seconds": round(elapsed, 1), "step_started_at": started})
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
        reasons = []
        if str(step.get("risk", "")).lower() == "high":
            reasons.append("High risk operation")
        try:
            confidence = float(step.get("confidence", 0.0))
            if confidence < self.auto_approve_confidence:
                reasons.append(f"Low confidence ({confidence:.0%})")
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
            lesson = (f"WORKFLOW REFLECTION: Goal '{goal}' finished with status {state.get('status')}. "
                      f"Success rate: {success_rate:.0%}. Worked tools: {', '.join(successful_tools) or 'none'}. "
                      f"Failed tools: {', '.join(failed_tools) or 'none'}. Replans: {state.get('replan_count', 0)}.")
            captured = None
            if self.memory_engine:
                try:
                    captured = self.memory_engine.add_memory(state["profile_id"], lesson, {"type": "workflow_reflection", "workflow_id": workflow_id, "success_rate": round(success_rate, 2), "replan_count": state.get("replan_count", 0)})
                except Exception:
                    logger.debug("Reflection memory write failed", exc_info=True)
            return {"workflow_id": workflow_id, "lesson": lesson, "patterns": {"success_rate": round(success_rate, 2), "successful_tools": successful_tools, "failed_tools": failed_tools, "replan_count": state.get("replan_count", 0)}, "captured": captured}

    def self_evolve(self, profile_id: str) -> Dict[str, Any]:
        states = {wid: state for wid, state in self.store.load_all().items() if state.get("profile_id") == profile_id and state.get("status") in TERMINAL_STATUSES}
        if not states:
            return {"profile_id": profile_id, "total_workflows": 0, "message": "No completed workflows found for this profile."}
        tool_successes: Dict[str, int] = {}
        tool_failures: Dict[str, int] = {}
        for state in states.values():
            for record in state.get("archived_history", []) + state.get("history", []):
                tool = record.get("tool") or "unknown"
                if record.get("status") == "success":
                    tool_successes[tool] = tool_successes.get(tool, 0) + 1
                elif record.get("status") == "failed":
                    tool_failures[tool] = tool_failures.get(tool, 0) + 1
        unreliable = sorted(tool_failures, key=tool_failures.get, reverse=True)
        reliable = sorted((tool for tool, count in tool_successes.items() if count >= 2 and tool_failures.get(tool, 0) == 0), key=tool_successes.get, reverse=True)
        for tool in unreliable:
            failures = tool_failures[tool]
            successes = tool_successes.get(tool, 0)
            if failures >= 3 and successes / max(1, successes + failures) < 0.2:
                self.store.mark_tool_broken(tool, f"Failed {failures} times; auto-flagged by self-evolution")
        completed = sum(1 for state in states.values() if state.get("status") == "completed")
        return {"profile_id": profile_id, "total_workflows": len(states), "completion_rate": round(completed / len(states), 2), "total_replans": sum(state.get("replan_count", 0) for state in states.values()), "reliable_tools": reliable, "unreliable_tools": unreliable, "tool_success_counts": tool_successes, "tool_failure_counts": tool_failures}


__all__ = ["AutonomousExecutor", "TERMINAL_STATUSES", "ACTIVE_STATUSES", "VALID_RESULT_STATUSES", "validate_plan"]
