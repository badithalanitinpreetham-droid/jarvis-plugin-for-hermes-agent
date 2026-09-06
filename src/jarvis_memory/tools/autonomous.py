"""Workflow state machine — client-driven step protocol.

Production hardening over the previous version:
- State is persisted via WorkflowStore (SQLite), not just an in-memory
  dict — a killed/restarted process resumes in-flight workflows instead
  of losing them. `active_workflows` is now a warm cache backed by the
  store, not the source of truth.
- start_workflow() is idempotent: calling it twice with the same
  workflow_id (e.g. a cron tick retried after a timeout) resumes the
  existing run instead of silently resetting progress.
- Steps can carry a `dedupe_key` (e.g. a content slug about to be
  published). Once a step with that key has been reported successful,
  it is never handed back to Hermes again for any workflow — protects
  against a crash-and-restart replaying a publish action that already
  went through.
- Plan structure is validated before a workflow starts, with a clear
  error instead of an IndexError/KeyError three calls later.
- Auto-replan: when a step fails and replan_count < max, the planner
  automatically generates a revised plan and continues execution.
- Workflow cancellation: stale or unwanted workflows can be cancelled.
- Stall detection: steps with no report after a timeout are flagged.

Still true from before: this module executes nothing itself. A server
has no way to reach back and invoke tools that live on the client
(Hermes). Only report_step_result(), called by Hermes after it actually
did the work, ever marks a step successful.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from ..config import CONFIG
from ..workflow_store import WorkflowStore

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "cancelled"}


def validate_plan(plan: Dict) -> Optional[str]:
    """Returns an error string if the plan is malformed, else None."""
    if not isinstance(plan, dict):
        return "plan must be an object"
    if "steps" not in plan or not isinstance(plan["steps"], list):
        return "plan.steps must be a list"
    if not plan["steps"]:
        return "plan.steps must not be empty"
    for i, step in enumerate(plan["steps"]):
        if not isinstance(step, dict):
            return f"step {i} must be an object"
        for required_key in ("id", "action", "confidence", "risk"):
            if required_key not in step:
                return f"step {i} missing required field '{required_key}'"
    return None


class AutonomousExecutor:
    """Tracks workflow state and gates steps by confidence/risk. Executes nothing itself."""

    def __init__(self, memory_engine=None, config: Dict = None, store: Optional[WorkflowStore] = None,
                 planner=None):
        self.memory_engine = memory_engine
        self.config = config or {}
        self.auto_approve_confidence = self.config.get("auto_approve_confidence", CONFIG.auto_approve_confidence)
        self.replan_max_retries = self.config.get("replan_max_retries", CONFIG.replan_max_retries)
        self.step_timeout = self.config.get("step_timeout", CONFIG.step_timeout)
        self.store = store or WorkflowStore(CONFIG.workflow_db_path)
        self.planner = planner  # Optional WorkflowPlanner for auto-replan

        # Warm cache from disk so an in-flight workflow survives a restart.
        self.active_workflows: Dict[str, Dict] = self.store.load_all()
        if self.active_workflows:
            logger.info("Resumed %d workflow(s) from persistent store.", len(self.active_workflows))

    def _persist(self, workflow_id: str):
        self.store.save(workflow_id, self.active_workflows[workflow_id])

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def start_workflow(self, workflow_id: str, plan: Dict, profile_id: str) -> Dict[str, Any]:
        # Idempotency: a retried cron tick calling start_workflow twice with
        # the same workflow_id should resume the run, not reset it.
        existing = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if existing and existing.get("status") not in TERMINAL_STATUSES:
            logger.info("start_workflow(%s) called again while still active — resuming, not resetting.", workflow_id)
            self.active_workflows[workflow_id] = existing
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
            "completed_steps": [],
            "failed_steps": [],
            "status": "running",
            "history": [],
            # --- new fields for JARVIS features ---
            "replan_count": 0,
            "replan_history": [],
            "step_started_at": None,
        }
        self._persist(workflow_id)
        return self.get_next_step(workflow_id)

    def get_next_step(self, workflow_id: str) -> Dict[str, Any]:
        """Return the step Hermes should execute next, or a terminal/paused status."""
        state = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}
        self.active_workflows[workflow_id] = state

        steps = state["plan"]["steps"]
        idx = state["next_index"]

        # Auto-skip any step whose dedupe_key has already succeeded in a
        # prior (possibly crashed) run — never re-hand a completed publish
        # action back to Hermes.
        while idx < len(steps):
            step = steps[idx]
            dedupe_key = step.get("dedupe_key")
            if dedupe_key and self.store.is_dedupe_done(dedupe_key):
                state["history"].append({
                    "step_id": step.get("id"),
                    "action": step.get("action"),
                    "status": "skipped_duplicate",
                    "output": f"dedupe_key '{dedupe_key}' already completed in a prior run",
                    "error": None,
                    "reported_at": self._now_iso(),
                })
                state["completed_steps"].append(step.get("id"))
                idx += 1
                state["next_index"] = idx
                state["approved_index"] = None
                continue
            break

        if idx >= len(steps):
            state["status"] = "completed" if not state["failed_steps"] else "completed_with_failures"
            state["step_started_at"] = None
            self._persist(workflow_id)
            return self._status_response(workflow_id)

        # Normal execution — grab next step
        current_step = steps[idx]

        # Parallel Racing: Check if this step is part of a race group
        race_group = current_step.get("race_group_id")
        if race_group:
            # Find all pending steps in this race group
            race_steps = []
            for i in range(idx, len(steps)):
                s = steps[i]
                if s.get("race_group_id") == race_group:
                    # Check if it was already cancelled or succeeded
                    hist = [h for h in state["history"] if h["step_id"] == s.get("id")]
                    if not hist:
                        race_steps.append(s)
                else:
                    # Race groups must be contiguous
                    break
            
            if race_steps:
                needs_approval = any(self._requires_approval(s) for s in race_steps)
                if needs_approval and state["approved_index"] != idx:
                    state["status"] = "awaiting_approval"
                    self._persist(workflow_id)
                    return {
                        "workflow_id": workflow_id,
                        "status": "awaiting_approval",
                        "race_group_id": race_group,
                        "parallel_steps": race_steps,
                        "message": "One or more parallel steps require approval.",
                        "requires_approval": True,
                    }

                if state["status"] == "running":
                    state["step_started_at"] = self._now_iso()
                    self._persist(workflow_id)
                
                return {
                    "workflow_id": workflow_id,
                    "status": "ready_to_execute" if state["status"] == "running" else state["status"],
                    "race_group_id": race_group,
                    "parallel_steps": race_steps,
                    "message": "Execute these steps simultaneously. The first successful result will automatically cancel the others.",
                    "requires_approval": False,
                }

        # Normal sequential return
        if state["status"] == "running":
            state["step_started_at"] = self._now_iso()
            self._persist(workflow_id)

        needs_approval = self._requires_approval(current_step)
        if needs_approval and state["approved_index"] != idx:
            state["status"] = "awaiting_approval"
            self._persist(workflow_id)
            return {
                "workflow_id": workflow_id,
                "status": "awaiting_approval",
                "step": current_step,
                "approval_reason": self._get_approval_reason(current_step),
            }

        state["status"] = "ready_to_execute"
        self._persist(workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "ready_to_execute",
            "step": current_step,
        }

    def approve_step(self, workflow_id: str, step_id: int) -> Dict[str, Any]:
        state = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}
        self.active_workflows[workflow_id] = state

        steps = state["plan"]["steps"]
        idx = state["next_index"]

        if idx >= len(steps):
            return {"error": "No pending steps to approve"}
            
        current_step = steps[idx]
        valid_ids = [current_step.get("id")]
        
        race_group = current_step.get("race_group_id")
        if race_group:
            for s in steps[idx+1:]:
                if s.get("race_group_id") == race_group:
                    valid_ids.append(s.get("id"))
                else:
                    break
                    
        if step_id not in valid_ids:
            return {"error": f"Step {step_id} is not the currently pending step"}

        state["approved_index"] = idx
        self._persist(workflow_id)
        return self.get_next_step(workflow_id)

    def report_step_result(
        self,
        workflow_id: str,
        step_id: int,
        status: str,
        output: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}
        self.active_workflows[workflow_id] = state

        steps = state["plan"]["steps"]
        idx = state["next_index"]

        if idx >= len(steps):
            return {"error": "Workflow is already completed"}

        current_step = steps[idx]
        race_group = current_step.get("race_group_id")
        
        valid_step = None
        if race_group:
            # Check if step_id matches any pending step in the race group
            for s in steps[idx:]:
                if s.get("race_group_id") == race_group:
                    if s.get("id") == step_id:
                        valid_step = s
                        break
                else:
                    break
        else:
            if current_step.get("id") == step_id:
                valid_step = current_step
                
        if not valid_step:
            return {"error": f"Step {step_id} is not the currently expected step (or valid race step)"}

        record = {
            "step_id": step_id,
            "action": valid_step.get("action"),
            "status": status,
            "output": output,
            "error": error,
            "reported_at": self._now_iso(),
        }
        state["history"].append(record)

        if status == "success":
            state["completed_steps"].append(step_id)
            if valid_step.get("dedupe_key"):
                self.store.mark_dedupe_done(valid_step["dedupe_key"], workflow_id, step_id)
            
            if race_group:
                # Cancel all other steps in the race group
                group_step_count = 0
                for s in steps[idx:]:
                    if s.get("race_group_id") == race_group:
                        group_step_count += 1
                        if s.get("id") != step_id:
                            state["history"].append({
                                "step_id": s.get("id"),
                                "action": s.get("action"),
                                "status": "cancelled_by_race_winner",
                                "output": f"Cancelled because step {step_id} won the race.",
                                "reported_at": self._now_iso()
                            })
                    else:
                        break
                # Advance index past the entire group
                state["next_index"] += group_step_count
            else:
                state["next_index"] += 1
                
        elif status == "failed":
            state["failed_steps"].append(step_id)
            
            if race_group:
                # Check if all steps in the race group have failed
                group_size = 0
                failures = 0
                for s in steps[idx:]:
                    if s.get("race_group_id") == race_group:
                        group_size += 1
                        if s.get("id") in state["failed_steps"]:
                            failures += 1
                    else:
                        break
                
                if failures == group_size:
                    # Entire race group failed. Trigger replan for the whole group.
                    state["next_index"] += group_size
                    # Proceed to trigger replan below
                else:
                    # Just record failure and wait for other racers
                    self._persist(workflow_id)
                    return self._status_response(workflow_id)
            else:
                state["next_index"] += 1

        state["approved_index"] = None
        state["step_started_at"] = None
        self._persist(workflow_id)

        if self.memory_engine:
            self.memory_engine.add_memory(
                state["profile_id"],
                f"Workflow {workflow_id} step {step_id} ({valid_step.get('action')}): {status}"
                + (f" — {error}" if error else ""),
                {"type": "execution_log" if status == "success" else "error_log", "workflow_id": workflow_id},
            )

        # --- Auto-replan on failure ---
        if status != "success" and self.planner and state.get("replan_count", 0) < self.replan_max_retries:
            return self._auto_replan(workflow_id, valid_step, error or "Unknown error")

        return self.get_next_step(workflow_id)

    def _auto_replan(self, workflow_id: str, failed_step: Dict, error: str) -> Dict[str, Any]:
        """Automatically replan when a step fails, if planner is available and cap not reached."""
        state = self.active_workflows[workflow_id]
        state["replan_count"] = state.get("replan_count", 0) + 1

        logger.info(
            "Auto-replanning workflow %s (attempt %d/%d) after step %s failed: %s",
            workflow_id, state["replan_count"], self.replan_max_retries,
            failed_step.get("id"), error[:100],
        )

        new_plan = self.planner.replan(
            original_plan=state["plan"],
            failed_step=failed_step.get("id"),
            error=error,
            history=state["history"],
        )

        plan_error = validate_plan(new_plan)
        if plan_error:
            logger.error("Replan produced invalid plan: %s — continuing with original", plan_error)
            state["replan_history"].append({
                "attempt": state["replan_count"],
                "failed_step_id": failed_step.get("id"),
                "error": error,
                "result": "invalid_plan",
                "at": self._now_iso(),
            })
            self._persist(workflow_id)
            return self.get_next_step(workflow_id)

        # Record replan event
        state["replan_history"].append({
            "attempt": state["replan_count"],
            "failed_step_id": failed_step.get("id"),
            "error": error,
            "result": "replanned",
            "new_steps_count": len(new_plan.get("steps", [])),
            "replan_reason": new_plan.get("replan_reason", ""),
            "at": self._now_iso(),
        })

        # Replace the plan and reset index to start from the new plan's first step
        state["plan"] = new_plan
        state["next_index"] = 0
        state["approved_index"] = None
        state["status"] = "running"
        state["completed_steps"] = []
        state["failed_steps"] = []
        state["archived_history"] = state.get("archived_history", []) + state.get("history", [])
        state["history"] = []
        self._persist(workflow_id)

        logger.info("Replan successful — new plan has %d steps", len(new_plan["steps"]))

        if self.memory_engine:
            self.memory_engine.add_memory(
                state["profile_id"],
                f"Workflow {workflow_id} auto-replanned (attempt {state['replan_count']}): "
                f"step {failed_step.get('id')} failed with '{error[:100]}'. "
                f"New plan has {len(new_plan['steps'])} steps.",
                {"type": "replan_event", "workflow_id": workflow_id},
            )

        return self.get_next_step(workflow_id)

    # --- Workflow cancellation ---

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> Dict[str, Any]:
        """Cancel an active workflow. Terminal workflows cannot be cancelled."""
        state = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}

        if state.get("status") in TERMINAL_STATUSES:
            return {"error": f"Workflow already in terminal state: {state['status']}"}

        state["status"] = "cancelled"
        state["step_started_at"] = None
        state["history"].append({
            "step_id": None,
            "action": "workflow_cancelled",
            "status": "cancelled",
            "output": reason or "Cancelled by user/system",
            "error": None,
            "reported_at": self._now_iso(),
        })
        self.active_workflows[workflow_id] = state
        self._persist(workflow_id)

        logger.info("Workflow %s cancelled: %s", workflow_id, reason or "no reason given")

        if self.memory_engine:
            self.memory_engine.add_memory(
                state["profile_id"],
                f"Workflow {workflow_id} ({state['plan'].get('goal', '')}) was cancelled: {reason}",
                {"type": "workflow_cancelled", "workflow_id": workflow_id},
            )

        return {
            "workflow_id": workflow_id,
            "status": "cancelled",
            "reason": reason or "Cancelled by user/system",
            "completed_steps": len(state["completed_steps"]),
            "total_steps": len(state["plan"]["steps"]),
        }

    # --- Stall detection ---

    def check_stalled_workflows(self, timeout_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        """Find workflows where the current step started more than timeout_seconds ago
        without a result being reported. Returns a list of stalled workflow summaries."""
        timeout = timeout_seconds or self.step_timeout
        stalled = []
        now = datetime.now(timezone.utc)

        # Check both in-memory cache and store
        all_workflows = dict(self.active_workflows)
        if self.store:
            all_workflows.update(self.store.load_all())
        for wf_id, state in all_workflows.items():
            if state.get("status") not in ("running",):
                continue
            started = state.get("step_started_at")
            if not started:
                continue
            try:
                started_dt = datetime.fromisoformat(started)
                # Handle naive datetimes
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                elapsed = (now - started_dt).total_seconds()
            except (ValueError, TypeError):
                continue

            if elapsed > timeout:
                steps = state["plan"]["steps"]
                idx = state.get("next_index", 0)
                current_step = steps[idx] if idx < len(steps) else None
                stalled.append({
                    "workflow_id": wf_id,
                    "profile_id": state.get("profile_id"),
                    "goal": state["plan"].get("goal", ""),
                    "stalled_step": current_step,
                    "elapsed_seconds": round(elapsed, 1),
                    "step_started_at": started,
                })

        if stalled:
            logger.warning("Found %d stalled workflow(s)", len(stalled))
        return stalled

    # --- Status helpers ---

    def _status_response(self, workflow_id: str) -> Dict[str, Any]:
        state = self.active_workflows[workflow_id]
        total = len(state["plan"]["steps"])
        return {
            "workflow_id": workflow_id,
            "status": state["status"],
            "total_steps": total,
            "completed": len(state["completed_steps"]),
            "failed": len(state["failed_steps"]),
            "replan_count": state.get("replan_count", 0),
            "history": state["history"],
        }

    def _requires_approval(self, step: Dict) -> bool:
        if "requires_approval" in step:
            if not step["requires_approval"]:
                return False
            return True
            
        if step.get("risk") == "high":
            return True
        if step.get("confidence", 0) < self.auto_approve_confidence:
            return True
        return False

    def _get_approval_reason(self, step: Dict) -> str:
        reasons = []
        if step.get("risk") == "high":
            reasons.append("High risk operation")
        if step.get("confidence", 0) < self.auto_approve_confidence:
            reasons.append(f"Low confidence ({step.get('confidence', 0):.0%})")
        if step.get("requires_approval", False):
            reasons.append("Explicitly marked for approval")
        return "; ".join(reasons) if reasons else "Unknown"

    def get_workflow_status(self, workflow_id: str) -> Optional[Dict]:
        return self.active_workflows.get(workflow_id) or self.store.load(workflow_id)

    def list_pending_approvals(self) -> list:
        """For a Hermes cron job to poll, since jarvis-memory (an MCP
        server) has no outbound channel of its own to push a notification
        through — this is the pollable surface that makes the approval
        gate actually visible instead of silently sitting in state."""
        return self.store.list_by_status("awaiting_approval")

    def reflect(self, workflow_id: str) -> Dict[str, Any]:
        """
        Self-evolution: distill a finished run into a STRUCTURED lesson.

        This is the WRITE side of the self-evolution loop. It stores:
        - What the goal was
        - Which tools/approaches succeeded vs failed
        - Success rate
        - Replan count (how hard this was)
        - Actionable lesson text

        The READ side is in WorkflowPlanner._recall_lessons(), which
        searches for these reflections before making a new plan.
        """
        state = self.active_workflows.get(workflow_id) or self.store.load(workflow_id)
        if state is None:
            return {"error": "Workflow not found"}

        history = state["history"]
        goal = state["plan"].get("goal", "")
        completed = state["completed_steps"]
        failed = state["failed_steps"]
        replan_count = state.get("replan_count", 0)
        total_steps = len(state["plan"]["steps"])

        # --- Extract structured patterns ---
        successful_actions = []
        failed_actions = []
        successful_tools = set()
        failed_tools = set()

        for h in history:
            action = h.get("action", "")
            if h["status"] == "success":
                successful_actions.append(action)
                # Extract tool from the plan step if available
                for s in state["plan"]["steps"]:
                    if s.get("id") == h.get("step_id"):
                        successful_tools.add(s.get("tool", "unknown"))
            elif h["status"] not in ("skipped_duplicate", "cancelled"):
                failed_actions.append(f"{action}: {h.get('error', 'unknown error')}")
                for s in state["plan"]["steps"]:
                    if s.get("id") == h.get("step_id"):
                        failed_tools.add(s.get("tool", "unknown"))

        success_rate = len(completed) / total_steps if total_steps > 0 else 0

        # --- Build lesson text (searchable by future recall) ---
        lesson_parts = [
            f"WORKFLOW REFLECTION: Goal '{goal}' finished with status {state['status']}.",
            f"Success rate: {success_rate:.0%} ({len(completed)}/{total_steps} steps).",
        ]

        if successful_actions:
            lesson_parts.append(
                f"SUCCEEDED approaches: {'; '.join(successful_actions[:5])}."
            )
        if successful_tools:
            lesson_parts.append(
                f"WORKED tools: {', '.join(sorted(successful_tools))}."
            )
        if failed_actions:
            lesson_parts.append(
                f"FAILED approaches: {'; '.join(failed_actions[:5])}."
            )
        if failed_tools:
            lesson_parts.append(
                f"AVOID tools (failed): {', '.join(sorted(failed_tools))}."
            )
        if replan_count > 0:
            lesson_parts.append(
                f"Required {replan_count} replan(s) — task was harder than expected."
            )

        # Add a forward-looking recommendation
        if state["status"] == "completed":
            lesson_parts.append("RECOMMENDATION: This approach works well for similar goals.")
        elif state["status"] == "completed_with_failures":
            lesson_parts.append("RECOMMENDATION: Try alternative approaches for the failed steps next time.")
        else:
            lesson_parts.append("RECOMMENDATION: This approach needs significant changes for similar goals.")

        lesson = " ".join(lesson_parts)

        # --- Structured metadata for machine parsing ---
        reflection_meta = {
            "type": "workflow_reflection",
            "workflow_id": workflow_id,
            "goal": goal,
            "status": state["status"],
            "success_rate": round(success_rate, 2),
            "successful_tools": sorted(successful_tools),
            "failed_tools": sorted(failed_tools),
            "replan_count": replan_count,
            "total_steps": total_steps,
            "completed_count": len(completed),
            "failed_count": len(failed),
        }

        captured = None
        if self.memory_engine:
            captured = self.memory_engine.add_memory(
                state["profile_id"],
                lesson,
                reflection_meta,
            )

        return {
            "workflow_id": workflow_id,
            "lesson": lesson,
            "patterns": {
                "success_rate": round(success_rate, 2),
                "successful_tools": sorted(successful_tools),
                "failed_tools": sorted(failed_tools),
                "successful_actions": successful_actions[:5],
                "failed_actions": failed_actions[:5],
                "replan_count": replan_count,
            },
            "captured": captured,
        }

    def self_evolve(self, profile_id: str) -> Dict[str, Any]:
        """Cross-workflow self-evolution analysis.

        Scans ALL completed workflows for a profile and builds an
        aggregate "what works / what doesn't" summary. This is useful
        for Hermes to call periodically to understand its own strengths
        and weaknesses.
        """
        all_states = self.store.load_all()

        profile_workflows = {
            wf_id: state for wf_id, state in all_states.items()
            if state.get("profile_id") == profile_id
            and state.get("status") in ("completed", "completed_with_failures", "failed", "cancelled")
        }

        if not profile_workflows:
            return {
                "profile_id": profile_id,
                "total_workflows": 0,
                "message": "No completed workflows found for this profile.",
            }

        # Aggregate patterns
        total = len(profile_workflows)
        fully_completed = sum(1 for s in profile_workflows.values() if s["status"] == "completed")
        tool_successes: Dict[str, int] = {}
        tool_failures: Dict[str, int] = {}
        total_replans = 0

        for state in profile_workflows.values():
            total_replans += state.get("replan_count", 0)
            for h in state.get("history", []):
                for s in state["plan"]["steps"]:
                    if s.get("id") == h.get("step_id"):
                        tool = s.get("tool", "unknown")
                        if h["status"] == "success":
                            tool_successes[tool] = tool_successes.get(tool, 0) + 1
                        elif h["status"] not in ("skipped_duplicate", "cancelled"):
                            tool_failures[tool] = tool_failures.get(tool, 0) + 1

        # Build evolution summary
        unreliable_tools = sorted(
            [t for t, c in tool_failures.items() if c >= 2],
            key=lambda t: tool_failures[t],
            reverse=True,
        )

        evolution = {
            "profile_id": profile_id,
            "total_workflows": total,
            "completion_rate": round(fully_completed / total, 2) if total else 0,
            "total_replans": total_replans,
            "reliable_tools": sorted(
                [t for t, c in tool_successes.items() if c >= 2 and t not in tool_failures],
                key=lambda t: tool_successes[t],
                reverse=True,
            ),
            "unreliable_tools": unreliable_tools,
            "tool_success_counts": tool_successes,
            "tool_failure_counts": tool_failures,
        }

        # --- Tool Repair: Auto-flag systematically broken tools ---
        if hasattr(self, "store"):
            for tool in unreliable_tools:
                fails = tool_failures.get(tool, 0)
                successes = tool_successes.get(tool, 0)
                # If a tool fails way more than it succeeds (e.g. 3+ fails and < 20% success rate)
                if fails >= 3 and (successes / (successes + fails)) < 0.2:
                    self.store.mark_tool_broken(tool, f"Failed {fails} times. Auto-flagged by self-evolution.")

        # Store the evolution summary in memory for future planning
        if self.memory_engine:
            summary_text = (
                f"SELF-EVOLUTION SUMMARY for {profile_id}: "
                f"{fully_completed}/{total} workflows fully completed ({evolution['completion_rate']:.0%}). "
                f"Reliable tools: {', '.join(evolution['reliable_tools']) or 'none identified yet'}. "
                f"Unreliable tools: {', '.join(evolution['unreliable_tools']) or 'none identified yet'}. "
                f"Total replans needed: {total_replans}."
            )
            self.memory_engine.add_memory(
                profile_id,
                summary_text,
                {"type": "self_evolution_summary"},
            )

        return evolution

