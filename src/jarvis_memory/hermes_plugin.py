"""General Hermes plugin facade for Jarvis intelligence and self-evolution.

This plugin adds lightweight routing/context and outcome observation. Memory recall is
owned by the native Jarvis MemoryProvider so Jarvis does not inject a second, competing
memory pipeline into Hermes.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, Iterable, Optional

from .experience_store import ExperienceStore
from .intelligence import JarvisIntelligence
from .orchestration.registry import HermesRegistry

_MAX_HOOK_CONTEXT = 4500


class JarvisPluginRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registry: Optional[HermesRegistry] = None
        self._store: Optional[ExperienceStore] = None
        self._intelligence: Optional[JarvisIntelligence] = None
        self._home = None
        self._started = False

    def start(self, hermes_home: Optional[str] = None) -> None:
        with self._lock:
            if self._started:
                return
            from pathlib import Path
            home = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
            self._home = home
            self._registry = HermesRegistry(hermes_home=home)
            self._store = ExperienceStore(str(home / ".jarvis" / "experience.db"))
            self._intelligence = JarvisIntelligence(self._registry, self._store)
            self._started = True

    @staticmethod
    def _last_user_message(messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "user":
                return str(item.get("content") or "")
        return ""

    def pre_llm_context(self, *, messages: Any = None, session_id: str = "", profile_id: str = "", **kwargs: Any) -> str:
        self.start(kwargs.get("hermes_home"))
        goal = self._last_user_message(messages)
        if not goal or self._intelligence is None:
            return ""
        decision = self._intelligence.classify(goal)
        if decision.mode == "simple":
            return ""
        packet = self._intelligence.context_for_goal(goal, profile_id=profile_id, limit=3)
        lines = [
            f"Jarvis routing: {decision.mode}. {decision.reason}.",
            "For complex work, continue to use Hermes' native Bots, subagents and Kanban; Jarvis only recommends organisation.",
        ]
        bots = packet.get("recommended_bots") or []
        if bots:
            lines.append("Relevant Hermes workers: " + ", ".join(str(b.get("name") or b.get("id")) for b in bots[:4]))
        lessons = packet.get("lessons") or []
        if lessons:
            lines.append("Relevant organisational lessons: " + " | ".join(str(x) for x in lessons[:4]))
        return "\n".join(lines)[:_MAX_HOOK_CONTEXT]

    def observe_tool(self, *, tool_name: str = "", result: Any = None, session_id: str = "", profile_id: str = "", **kwargs: Any) -> None:
        self.start(kwargs.get("hermes_home"))
        if self._intelligence is None:
            return
        result_text = str(result or "")
        status = "success" if not any(x in result_text.lower() for x in ("error", "exception", "failed")) else "failed"
        self._intelligence.observe_outcome(
            goal=f"Hermes tool: {tool_name}",
            status=status,
            profile_id=profile_id,
            session_id=session_id,
            strategy="hermes_tool",
            evidence={"tool_name": tool_name, "result": result_text[:5000]},
        )

    def observe_subagent(self, *, task: str = "", result: Any = None, session_id: str = "", profile_id: str = "", **kwargs: Any) -> None:
        self.start(kwargs.get("hermes_home"))
        if self._intelligence is None:
            return
        result_text = str(result or "")
        self._intelligence.observe_outcome(
            goal=task,
            status="success" if result_text.strip() else "failed",
            profile_id=profile_id,
            session_id=session_id,
            strategy="hermes_subagent",
            evidence={"result": result_text[:7000]},
        )

    def close(self) -> None:
        with self._lock:
            if self._intelligence is not None:
                self._intelligence.close()
            elif self._store is not None:
                self._store.close()
            self._registry = None
            self._store = None
            self._intelligence = None
            self._started = False


_RUNTIME = JarvisPluginRuntime()


def _on_session_start(**kwargs: Any) -> None:
    _RUNTIME.start(kwargs.get("hermes_home"))


def _on_pre_llm_call(**kwargs: Any):
    context = _RUNTIME.pre_llm_context(**kwargs)
    return {"context": context} if context else None


def _on_post_tool_call(**kwargs: Any) -> None:
    _RUNTIME.observe_tool(**kwargs)


def _on_subagent_stop(**kwargs: Any) -> None:
    _RUNTIME.observe_subagent(**kwargs)


def _on_session_end(**kwargs: Any) -> None:
    # Do not close the process-global runtime here: gateway/CLI may reuse it for another session.
    return None


def _jarvis_orchestrate(arguments: Dict[str, Any], **kwargs: Any) -> str:
    _RUNTIME.start(kwargs.get("hermes_home"))
    goal = str(arguments.get("goal") or "").strip()
    if not goal:
        return json.dumps({"error": "goal is required"})
    packet = _RUNTIME._intelligence.context_for_goal(goal, profile_id=str(arguments.get("profile_id") or "default"), limit=6) if _RUNTIME._intelligence else {}
    return json.dumps(packet, ensure_ascii=False, default=str)


def _jarvis_record_outcome(arguments: Dict[str, Any], **kwargs: Any) -> str:
    _RUNTIME.start(kwargs.get("hermes_home"))
    if _RUNTIME._intelligence is None:
        return json.dumps({"error": "Jarvis intelligence unavailable"})
    outcome_id = _RUNTIME._intelligence.observe_outcome(
        goal=str(arguments.get("goal") or ""),
        status=str(arguments.get("status") or "success"),
        profile_id=str(arguments.get("profile_id") or "default"),
        session_id=str(arguments.get("session_id") or ""),
        strategy=str(arguments.get("strategy") or ""),
        bots=arguments.get("bots") if isinstance(arguments.get("bots"), list) else [],
        deliverable=str(arguments.get("deliverable") or ""),
        quality=arguments.get("quality"),
        evidence=arguments.get("evidence") if isinstance(arguments.get("evidence"), dict) else {},
        lessons=arguments.get("lessons") if isinstance(arguments.get("lessons"), list) else [],
    )
    return json.dumps({"recorded": True, "outcome_id": outcome_id})


def register(ctx: Any) -> None:
    """Native Hermes plugin registration. No core Hermes files are modified."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("subagent_stop", _on_subagent_stop)
    ctx.register_hook("on_session_end", _on_session_end)
    try:
        ctx.register_tool("jarvis_orchestrate", "Ask Jarvis to analyse a goal and recommend Hermes workforce organisation", _jarvis_orchestrate)
        ctx.register_tool("jarvis_record_outcome", "Record a completed work outcome so Jarvis can learn from it", _jarvis_record_outcome)
    except TypeError:
        # Hermes versions before the current register_tool signature may accept a schema object.
        pass


__all__ = ["JarvisPluginRuntime", "register"]
