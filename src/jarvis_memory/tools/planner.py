"""Workflow planner with optional OpenAI-compatible local/cloud backend."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import CONFIG

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


def validate_plan(plan: Dict[str, Any]) -> Optional[str]:
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        return "plan.steps must be a non-empty list"
    ids = set()
    for i, step in enumerate(plan["steps"]):
        if not isinstance(step, dict):
            return f"step {i} must be an object"
        for key in ("id", "action", "confidence", "risk"):
            if key not in step:
                return f"step {i} missing '{key}'"
        sid = str(step["id"])
        if not sid or sid in ids:
            return f"duplicate or empty step id at index {i}"
        ids.add(sid)
        try:
            confidence = float(step["confidence"])
        except (TypeError, ValueError):
            return f"step {i} confidence must be numeric"
        if not 0 <= confidence <= 1:
            return f"step {i} confidence must be between 0 and 1"
        if str(step["risk"]).lower() not in {"low", "medium", "high"}:
            return f"step {i} risk must be low/medium/high"
    return None


class OpenAICompatiblePlannerClient:
    """Small adapter for Ollama or another OpenAI-compatible endpoint."""
    def __init__(self, url: str, model: str, api_key: str = ""):
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = httpx.Client(timeout=120.0)

    def chat_completion(self, messages: List[Dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = self.client.post(f"{self.url}/chat/completions", headers=headers,
                             json={"model": self.model, "messages": messages,
                                   "temperature": 0.1, "response_format": {"type": "json_object"}})
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        self.client.close()


class WorkflowPlanner:
    def __init__(self, llm_client=None, memory_engine=None, store=None):
        if llm_client is None and CONFIG.planner_llm_url and CONFIG.planner_llm_model:
            llm_client = OpenAICompatiblePlannerClient(CONFIG.planner_llm_url, CONFIG.planner_llm_model, CONFIG.planner_llm_key)
        self.llm_client = llm_client
        self.memory_engine = memory_engine
        self.store = store

    def _recall_lessons(self, goal: str, profile_id: str) -> List[str]:
        if not self.memory_engine:
            return []
        try:
            memories = self.memory_engine.search_memory(profile_id, f"workflow reflection lesson: {goal}", limit=5)
            lessons = []
            for mem in memories or []:
                text = mem.get("text", mem.get("content", mem.get("memory", ""))) if isinstance(mem, dict) else str(mem)
                if text and any(k in text.lower() for k in ("workflow", "failed", "succeeded", "lesson", "replan")):
                    lessons.append(text[:400])
            return lessons[:5]
        except Exception:
            logger.debug("Lesson recall failed", exc_info=True)
            return []

    def create_plan(self, goal: str, profile_id: str, context: str = "") -> Dict[str, Any]:
        lessons = self._recall_lessons(goal, profile_id)
        prompt = f"""Create a concrete execution plan for this goal.
Goal: {goal}
Profile: {profile_id}
Context: {context}
Past lessons: {json.dumps(lessons)}
Each step must contain id, action, tool, parameters, confidence (0..1), risk (low/medium/high), requires_approval.
Do not invent a tool for code repair: when a tool is broken, use tool=hermes_code_repair with parameters containing tool_name and reason.
Output JSON only with goal, steps, estimated_steps and success_criteria."""
        plan = None
        if self.llm_client:
            try:
                plan = _extract_json(self.llm_client.chat_completion([
                    {"role": "system", "content": "You are a precise workflow planner. Output JSON only."},
                    {"role": "user", "content": prompt},
                ]))
            except Exception:
                logger.exception("Planner LLM call failed")
        if not plan:
            plan = self._heuristic_plan(goal, lessons)
        err = validate_plan(plan)
        if err:
            return {"error": f"Invalid plan: {err}", "goal": goal, "steps": [], "fallback_mode": True}

        if self.store:
            broken = set(self.store.get_broken_tools())
            used = {s.get("tool") for s in plan["steps"]}
            for tool in sorted(used & broken):
                plan["steps"].insert(0, {
                    "id": f"repair-{tool}",
                    "action": f"Diagnose and repair broken Hermes tool '{tool}'.",
                    "tool": "hermes_code_repair",
                    "parameters": {"tool_name": tool, "reason": "Tool is flagged as broken in Jarvis state."},
                    "confidence": 0.5,
                    "risk": "high",
                    "requires_approval": True,
                })
            err = validate_plan(plan)
            if err:
                return {"error": f"Repair injection produced invalid plan: {err}", "goal": goal, "steps": [], "fallback_mode": True}
        if lessons:
            plan["lessons_applied"] = lessons
        plan["estimated_steps"] = len(plan["steps"])
        return plan

    def _heuristic_plan(self, goal: str, lessons: List[str]) -> Dict[str, Any]:
        risky = any("failed" in x.lower() or "error" in x.lower() for x in lessons)
        return {"goal": goal, "steps": [
            {"id": 1, "action": f"Analyse the goal: {goal}", "tool": "analyze", "parameters": {"goal": goal}, "confidence": 0.9, "risk": "low", "requires_approval": False},
            {"id": 2, "action": "Execute the primary task using the best available tools.", "tool": "execute", "parameters": {"goal": goal}, "confidence": 0.75 if risky else 0.9, "risk": "high" if risky else "medium", "requires_approval": risky},
        ], "estimated_steps": 2, "success_criteria": "Task completed and verified."}

    def replan(self, original_plan: Dict[str, Any], failed_step: Any, error: str, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        history = history or []
        if self.llm_client:
            try:
                plan = _extract_json(self.llm_client.chat_completion([
                    {"role": "system", "content": "Replan a failed workflow. Output JSON only."},
                    {"role": "user", "content": json.dumps({"goal": original_plan.get("goal"), "plan": original_plan,
                        "failed_step": failed_step, "error": error, "history": history[-10:]})},
                ]))
                if plan and not validate_plan(plan):
                    return plan
            except Exception:
                logger.exception("LLM replan failed")
        return self._heuristic_replan(original_plan, failed_step, error)

    def _heuristic_replan(self, original_plan: Dict[str, Any], failed_step: Any, error: str) -> Dict[str, Any]:
        steps = original_plan.get("steps", [])
        idx = next((i for i, s in enumerate(steps) if str(s.get("id")) == str(failed_step)), None)
        if idx is None:
            return {"goal": original_plan.get("goal", ""), "steps": [
                {"id": "recovery-1", "action": "Analyse the failure and recover.", "tool": "analyze", "parameters": {"error": error[:500]}, "confidence": 0.5, "risk": "medium", "requires_approval": True},
                {"id": "recovery-2", "action": "Retry the goal using an alternative approach.", "tool": "execute", "parameters": {"goal": original_plan.get("goal", ""), "retry_reason": error[:500]}, "confidence": 0.6, "risk": "medium", "requires_approval": True},
            ], "estimated_steps": 2, "success_criteria": original_plan.get("success_criteria", "Task completed"), "replan_reason": error[:200]}
        failed = dict(steps[idx])
        failed["id"] = f"retry-{failed.get('id')}-{idx+1}"
        failed["action"] = f"[RETRY] {failed.get('action', 'failed step')}"
        failed["parameters"] = {**failed.get("parameters", {}), "_retry_reason": error[:300], "_alternative": True}
        failed["confidence"] = max(0.3, float(failed.get("confidence", 0.7)) - 0.2)
        failed["requires_approval"] = True
        new_steps = [dict(s) for s in steps[:idx]] + [failed] + [dict(s) for s in steps[idx + 1:]]
        return {"goal": original_plan.get("goal", ""), "steps": new_steps, "estimated_steps": len(new_steps),
                "success_criteria": original_plan.get("success_criteria", "Task completed"), "replan_reason": f"Step {failed_step} failed: {error[:200]}"}
