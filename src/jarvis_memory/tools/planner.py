"""Workflow Planner - Breaks high-level goals into executable steps.

Now with real replanning: when a step fails, replan() generates a new
plan that retries the failed step with an alternative approach and
preserves remaining steps — instead of returning a useless generic
2-step heuristic.
"""

import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Optional[dict]:
    """Strip markdown fences / stray prose and parse the first JSON object.

    The original code did `json.loads(response)` directly — any LLM that
    wraps its output in ```json fences (which most do by default) crashed
    planning outright and fell through to the heuristic fallback silently.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first_line, rest = cleaned.split("\n", 1)
            if first_line.strip().lower() in ("json", ""):
                cleaned = rest
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return None


class WorkflowPlanner:
    """Plans complex tasks into autonomous execution steps.

    Self-evolution: when a memory_engine is wired, the planner recalls
    past workflow reflections before building a new plan. Lessons like
    "approach X always fails for goal type Y" or "tool Z worked well"
    are extracted and used to adjust confidence scores and avoid
    repeating known-bad approaches.
    """

    def __init__(self, llm_client=None, memory_engine=None, store=None):
        self.llm_client = llm_client
        self.memory_engine = memory_engine
        self.store = store

    def create_plan(self, goal: str, profile_id: str, context: str = "") -> Dict[str, Any]:
        # --- Self-evolution: recall past lessons before planning ---
        past_lessons = self._recall_lessons(goal, profile_id)
        lessons_context = ""
        if past_lessons:
            lessons_context = "\n\nPast experience (learned from previous workflows):\n"
            for lesson in past_lessons:
                lessons_context += f"  - {lesson}\n"
            logger.info("Recalled %d past lesson(s) for goal planning", len(past_lessons))

        prompt = f"""
You are an autonomous agent planner. Break down this goal into concrete executable steps.

Goal: {goal}
Profile: {profile_id}
Context: {context}{lessons_context}

For each step, determine:
1. The action to take
2. Required tools/parameters
3. Confidence score (0.0-1.0) - how certain you are this will succeed
4. Risk level (low/medium/high) - potential impact of failure
5. Whether it requires human approval (true/false)

Output ONLY valid JSON in this format:
{{
  "goal": "restated goal",
  "steps": [
    {{
      "id": 1,
      "action": "description",
      "tool": "tool_name",
      "parameters": {{}},
      "confidence": 0.95,
      "risk": "low",
      "requires_approval": false
    }}
  ],
  "estimated_steps": 5,
  "success_criteria": "how to know the goal is achieved"
}}
"""

        try:
            if self.llm_client:
                response = self.llm_client.chat_completion([
                    {"role": "system", "content": "Output ONLY valid JSON. No markdown."},
                    {"role": "user", "content": prompt}
                ])
                plan = _extract_json(response)
                if plan is None:
                    logger.error("Planner LLM did not return parseable JSON; falling back to heuristic plan")
                    plan = self._heuristic_plan(goal, profile_id, past_lessons)
            else:
                plan = self._heuristic_plan(goal, profile_id, past_lessons)

            if not self._validate_plan(plan):
                raise ValueError("Invalid plan structure")

            # --- Tool Repair: Auto-inject repair steps if tools are broken ---
            if self.store:
                broken_tools = self.store.get_broken_tools()
                tools_in_plan = set(s.get("tool") for s in plan.get("steps", []) if s.get("tool"))
                overlap = tools_in_plan.intersection(broken_tools)
                if overlap:
                    logger.warning(f"Plan uses broken tools: {overlap}. Injecting repair phase.")
                    repair_steps = []
                    for i, btool in enumerate(overlap):
                        repair_steps.append({
                            "id": -100 + i, # Negative ID to prepend before normal steps
                            "action": f"TOOL REPAIR: Diagnose and fix broken tool '{btool}'. Read its source code and patch the bug.",
                            "tool": "jarvis_edit_html", # Placeholder for whatever coding tool Hermes uses
                            "parameters": {"file": btool},
                            "confidence": 0.5,
                            "risk": "high",
                            "requires_approval": True
                        })
                    plan["steps"] = repair_steps + plan["steps"]

            # Attach lessons used for transparency
            if past_lessons:
                plan["lessons_applied"] = past_lessons

            logger.info(f"Created plan with {len(plan.get('steps', []))} steps")
            return plan

        except Exception as e:
            logger.error(f"Planning failed: {e}")
            return {
                "error": str(e),
                "goal": goal,
                "steps": [],
                "fallback_mode": True
            }

    def _recall_lessons(self, goal: str, profile_id: str) -> List[str]:
        """Search memory for past workflow reflections relevant to this goal.

        Returns a list of human-readable lesson strings extracted from
        past reflections. This is the READ side of the self-evolution loop
        (the WRITE side is reflect() in AutonomousExecutor).
        """
        if not self.memory_engine:
            return []

        try:
            # Search for workflow reflections related to this goal
            query = f"workflow reflection lesson: {goal}"
            if hasattr(self.memory_engine, "search_memory"):
                memories = self.memory_engine.search_memory(profile_id, query, limit=5)
            elif hasattr(self.memory_engine, "recall"):
                memories = self.memory_engine.recall(profile_id, query, limit=5)
            else:
                return []
                
            if not memories:
                return []

            lessons = []
            for mem in memories:
                # Extract the text content from memory results
                text = ""
                if isinstance(mem, dict):
                    text = mem.get("text", mem.get("content", mem.get("memory", "")))
                elif isinstance(mem, str):
                    text = mem

                if not text:
                    continue

                # Only include workflow-related lessons
                text_lower = text.lower()
                if any(kw in text_lower for kw in (
                    "workflow", "step", "failed", "succeeded", "lesson",
                    "replanned", "completed", "approach", "error",
                )):
                    # Truncate long lessons
                    lessons.append(text[:300])

            return lessons[:5]  # Max 5 lessons

        except Exception as e:
            logger.debug("Lesson recall failed (non-fatal): %s", e)
            return []

    def _heuristic_plan(self, goal: str, profile_id: str,
                        past_lessons: Optional[List[str]] = None) -> Dict[str, Any]:
        """Heuristic-based planning with self-evolution adjustments.

        If past lessons mention failures for this type of task, confidence
        is lowered and approval gates are added. If past lessons mention
        successes, confidence is raised.
        """
        # Analyze past lessons for confidence adjustment
        confidence_boost = 0.0
        add_approval = False
        if past_lessons:
            for lesson in past_lessons:
                lower = lesson.lower()
                if "failed" in lower or "error" in lower:
                    confidence_boost -= 0.1
                    add_approval = True  # Gate it since similar tasks failed before
                elif "succeeded" in lower or "completed" in lower:
                    confidence_boost += 0.05

        base_conf_1 = max(0.3, min(1.0, 0.95 + confidence_boost))
        base_conf_2 = max(0.3, min(1.0, 0.90 + confidence_boost))

        plan = {
            "goal": goal,
            "steps": [
                {
                    "id": 1,
                    "action": f"Analyze goal: {goal}",
                    "tool": "analyze",
                    "parameters": {"query": goal},
                    "confidence": round(base_conf_1, 2),
                    "risk": "low",
                    "requires_approval": False
                },
                {
                    "id": 2,
                    "action": "Execute primary task",
                    "tool": "execute",
                    "parameters": {"goal": goal},
                    "confidence": round(base_conf_2, 2),
                    "risk": "medium" if not add_approval else "high",
                    "requires_approval": bool(add_approval)
                }
            ],
            "estimated_steps": 2,
            "success_criteria": "Task completed successfully"
        }

        if past_lessons:
            plan["informed_by_lessons"] = len(past_lessons)

        return plan

    def _validate_plan(self, plan: Dict) -> bool:
        required_keys = ["goal", "steps"]
        if not all(key in plan for key in required_keys):
            return False
        if not isinstance(plan.get("steps"), list):
            return False
        for step in plan["steps"]:
            if not all(key in step for key in ["id", "action", "confidence", "risk"]):
                return False
        return True

    def replan(
        self,
        original_plan: Dict,
        failed_step: int,
        error: str,
        history: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Create a new plan after a step failure.

        Strategy:
        1. If an LLM is wired, build a context-rich prompt with the original
           plan, which step failed, what error occurred, and execution history,
           then ask it for a revised plan.
        2. If no LLM (or if the LLM call fails), use a deterministic heuristic:
           - Retry the failed step with approach="alternative"
           - Preserve all remaining steps after the failed one
           - Re-number step IDs for clean sequencing
        """
        goal = original_plan.get("goal", "")
        steps = original_plan.get("steps", [])
        history = history or []

        # --- LLM-backed replanning ---
        if self.llm_client:
            plan = self._llm_replan(original_plan, failed_step, error, history)
            if plan and self._validate_plan(plan):
                logger.info("LLM replan succeeded with %d steps", len(plan["steps"]))
                return plan
            logger.warning("LLM replan failed or invalid; falling back to heuristic replan")

        # --- Deterministic heuristic replan ---
        return self._heuristic_replan(original_plan, failed_step, error, history)

    def _llm_replan(
        self,
        original_plan: Dict,
        failed_step: int,
        error: str,
        history: List[Dict],
    ) -> Optional[Dict]:
        """Ask the LLM for a revised plan given the failure context."""
        goal = original_plan.get("goal", "")
        steps = original_plan.get("steps", [])

        # Build a concise history summary
        history_lines = []
        for h in history[-10:]:  # Last 10 entries max
            status_icon = "✅" if h.get("status") == "success" else "❌"
            line = f"  {status_icon} Step {h.get('step_id')}: {h.get('action')} → {h.get('status')}"
            if h.get("error"):
                line += f" ({h['error'][:100]})"
            history_lines.append(line)
        history_text = "\n".join(history_lines) if history_lines else "  (no history)"

        # Find the failed step details
        failed_step_detail = None
        for s in steps:
            if s.get("id") == failed_step:
                failed_step_detail = s
                break
        failed_desc = json.dumps(failed_step_detail, indent=2) if failed_step_detail else f"Step {failed_step}"

        prompt = f"""A workflow step has failed. Create a REVISED plan to achieve the same goal using a different approach for the failed step.

ORIGINAL GOAL: {goal}

FAILED STEP:
{failed_desc}

ERROR: {error}

EXECUTION HISTORY:
{history_text}

Requirements:
1. The failed step must be retried with a DIFFERENT approach/tool/parameters
2. All remaining steps after the failed one should be preserved or adapted
3. Do NOT repeat already-completed steps
4. Each step needs: id, action, tool, parameters, confidence, risk

Output ONLY valid JSON in this format:
{{
  "goal": "{goal}",
  "steps": [...],
  "estimated_steps": N,
  "success_criteria": "...",
  "replan_reason": "brief explanation of the new approach"
}}
"""
        try:
            response = self.llm_client.chat_completion([
                {"role": "system", "content": "Output ONLY valid JSON. No markdown. You are replanning a failed workflow step."},
                {"role": "user", "content": prompt}
            ])
            return _extract_json(response)
        except Exception as e:
            logger.error("LLM replan call failed: %s", e)
            return None

    def _heuristic_replan(
        self,
        original_plan: Dict,
        failed_step: int,
        error: str,
        history: List[Dict],
    ) -> Dict[str, Any]:
        """Deterministic replan without LLM.

        Strategy:
        - Retry the failed step with an "alternative" approach marker
        - Lower confidence (the retry is less certain)
        - Preserve all remaining unfailed steps
        - Re-number IDs cleanly
        """
        goal = original_plan.get("goal", "")
        steps = original_plan.get("steps", [])

        # Find the failed step and everything after it
        failed_idx = None
        for i, step in enumerate(steps):
            if step.get("id") == failed_step:
                failed_idx = i
                break

        if failed_idx is None:
            # Failed step not found — return a minimal recovery plan
            return {
                "goal": goal,
                "steps": [
                    {
                        "id": 1,
                        "action": f"Recover from failure: {error[:200]}",
                        "tool": "analyze",
                        "parameters": {"error": error, "goal": goal},
                        "confidence": 0.5,
                        "risk": "medium",
                        "requires_approval": True,
                        "approach": "recovery",
                    },
                    {
                        "id": 2,
                        "action": f"Re-attempt goal: {goal}",
                        "tool": "execute",
                        "parameters": {"goal": goal},
                        "confidence": 0.6,
                        "risk": "medium",
                        "requires_approval": True,
                        "approach": "alternative",
                    },
                ],
                "estimated_steps": 2,
                "success_criteria": original_plan.get("success_criteria", "Task completed"),
                "replan_reason": f"Step {failed_step} failed: {error[:200]}",
            }

        failed_step_data = steps[failed_idx]
        remaining_steps = steps[failed_idx + 1:]

        # Build the retry step — same action but with alternative approach marker
        retry_confidence = max(0.3, failed_step_data.get("confidence", 0.7) - 0.2)
        retry_step = {
            "id": failed_step_data.get("id", failed_step),
            "action": f"[RETRY] {failed_step_data.get('action', 'Unknown action')}",
            "tool": failed_step_data.get("tool", "execute"),
            "parameters": {
                **failed_step_data.get("parameters", {}),
                "_retry_reason": error[:200],
                "_original_step_id": failed_step,
            },
            "confidence": round(retry_confidence, 2),
            "risk": failed_step_data.get("risk", "medium"),
            "requires_approval": True,  # Always gate retries for safety
            "approach": "alternative",
        }
        
        if "dedupe_key" in failed_step_data:
            retry_step["dedupe_key"] = failed_step_data["dedupe_key"]
            
        if "race_group_id" in failed_step_data:
            retry_step["race_group_id"] = failed_step_data["race_group_id"]

        # Preserve remaining steps with their original IDs
        new_steps = [retry_step]
        for step in remaining_steps:
            preserved = dict(step)
            new_steps.append(preserved)

        return {
            "goal": goal,
            "steps": new_steps,
            "estimated_steps": len(new_steps),
            "success_criteria": original_plan.get("success_criteria", "Task completed"),
            "replan_reason": f"Step {failed_step} ({failed_step_data.get('action', '')}) failed: {error[:200]}",
        }
