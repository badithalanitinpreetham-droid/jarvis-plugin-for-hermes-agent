"""Native Hermes plugin facade for Jarvis intelligence and owned local services."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .experience_store import ExperienceStore
from .intelligence import JarvisIntelligence
from .orchestration.registry import HermesRegistry
from .tencent_runtime import TencentRuntime, get_tencent_runtime

_MAX_HOOK_CONTEXT = 4500


class JarvisPluginRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registry: Optional[HermesRegistry] = None
        self._store: Optional[ExperienceStore] = None
        self._intelligence: Optional[JarvisIntelligence] = None
        self._home: Optional[Path] = None
        self._started = False
        self._services: TencentRuntime = get_tencent_runtime()

    def start(self, hermes_home: Optional[str] = None) -> None:
        with self._lock:
            requested = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
            if self._started and self._home == requested:
                return
            if self._started and self._home != requested:
                self.close()
            self._home = requested
            self._services.start(str(requested))
            self._registry = HermesRegistry(root=requested)
            self._store = ExperienceStore(str(requested / ".jarvis" / "experience.db"))
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

    def pre_llm_context(self, *, messages: Any = None, profile_id: str = "", **kwargs: Any) -> str:
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
            "For complex work, continue to use Hermes' native Bots, subagents and Kanban; Jarvis recommends organisation and memory only.",
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
        if self._intelligence is None or not tool_name:
            return
        result_text = str(result or "")
        failed = isinstance(result, dict) and bool(result.get("error"))
        if isinstance(result, str):
            stripped = result.lstrip().lower()
            failed = failed or stripped.startswith(("error:", "exception:", "traceback"))
        self._intelligence.observe_outcome(
            goal=f"Hermes tool: {tool_name}", status="failed" if failed else "success",
            profile_id=profile_id, session_id=session_id, strategy="hermes_tool",
            evidence={"tool_name": tool_name, "result": result_text[:5000]},
        )

    def observe_subagent(self, *, task: str = "", result: Any = None, session_id: str = "", profile_id: str = "", **kwargs: Any) -> None:
        self.start(kwargs.get("hermes_home"))
        if self._intelligence is None:
            return
        result_text = str(result or "")
        failed = isinstance(result, dict) and bool(result.get("error"))
        self._intelligence.observe_outcome(
            goal=task, status="failed" if failed or not result_text.strip() else "success",
            profile_id=profile_id, session_id=session_id, strategy="hermes_subagent",
            evidence={"result": result_text[:7000]},
        )

    def close(self) -> None:
        with self._lock:
            if not self._started:
                return
            try:
                if self._intelligence is not None:
                    self._intelligence.close()
                elif self._store is not None:
                    self._store.close()
            finally:
                self._registry = None
                self._store = None
                self._intelligence = None
                self._home = None
                self._services.close()
                self._started = False


_RUNTIME = JarvisPluginRuntime()
atexit.register(_RUNTIME.close)


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
    return None


def _jarvis_orchestrate(arguments: Dict[str, Any], **kwargs: Any) -> str:
    _RUNTIME.start(kwargs.get("hermes_home"))
    goal = str(arguments.get("goal") or "").strip()
    if not goal:
        return json.dumps({"error": "goal is required"})
    packet = _RUNTIME._intelligence.context_for_goal(
        goal, profile_id=str(arguments.get("profile_id") or "default"), limit=6
    ) if _RUNTIME._intelligence else {}
    return json.dumps(packet, ensure_ascii=False, default=str)


def _jarvis_record_outcome(arguments: Dict[str, Any], **kwargs: Any) -> str:
    _RUNTIME.start(kwargs.get("hermes_home"))
    if _RUNTIME._intelligence is None:
        return json.dumps({"error": "Jarvis intelligence unavailable"})
    outcome_id = _RUNTIME._intelligence.observe_outcome(
        goal=str(arguments.get("goal") or ""), status=str(arguments.get("status") or "success"),
        profile_id=str(arguments.get("profile_id") or "default"), session_id=str(arguments.get("session_id") or ""),
        strategy=str(arguments.get("strategy") or ""),
        bots=arguments.get("bots") if isinstance(arguments.get("bots"), list) else [],
        deliverable=str(arguments.get("deliverable") or ""), quality=arguments.get("quality"),
        evidence=arguments.get("evidence") if isinstance(arguments.get("evidence"), dict) else {},
        lessons=arguments.get("lessons") if isinstance(arguments.get("lessons"), list) else [],
    )
    return json.dumps({"recorded": True, "outcome_id": outcome_id})


def _jarvis_runtime(arguments: Dict[str, Any], **kwargs: Any) -> str:
    action = str(arguments.get("action") or "status").strip().lower()
    if action == "start":
        _RUNTIME.start(kwargs.get("hermes_home"))
    elif action in {"stop", "shutdown"}:
        _RUNTIME.close()
    elif action != "status":
        return json.dumps({"error": "action must be status, start, or stop"})
    return json.dumps(_RUNTIME._services.status(kwargs.get("hermes_home")), ensure_ascii=False)


def _setup_jarvis_start(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", choices=["jarvis"], help="Start Jarvis and its owned local services")


def _setup_jarvis_stop(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", choices=["jarvis"], help="Stop Jarvis and its owned local services")


def _handle_jarvis_start(args: argparse.Namespace) -> int:
    get_tencent_runtime().start(os.environ.get("HERMES_HOME"), force=True)
    state = get_tencent_runtime().status(os.environ.get("HERMES_HOME"))
    print("Jarvis started.")
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def _handle_jarvis_stop(args: argparse.Namespace) -> int:
    get_tencent_runtime().stop(os.environ.get("HERMES_HOME"), disable=True)
    state = get_tencent_runtime().status(os.environ.get("HERMES_HOME"))
    print("Jarvis stopped.")
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


def _handle_command(raw_args: str) -> Optional[str]:
    action = raw_args.strip().lower() or "status"
    return _jarvis_runtime({"action": action})


def register(ctx: Any) -> None:
    """Register Jarvis with Hermes without starting services during CLI discovery."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("subagent_stop", _on_subagent_stop)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_tool(name="jarvis_orchestrate", toolset="jarvis", schema=_ORCHESTRATE_SCHEMA,
                      handler=_jarvis_orchestrate,
                      description="Analyse a goal and recommend how Hermes should organise its existing workforce.")
    ctx.register_tool(name="jarvis_record_outcome", toolset="jarvis", schema=_RECORD_OUTCOME_SCHEMA,
                      handler=_jarvis_record_outcome,
                      description="Record a completed work outcome so Jarvis can learn from it.")
    ctx.register_tool(name="jarvis_runtime", toolset="jarvis", schema=_RUNTIME_SCHEMA,
                      handler=_jarvis_runtime,
                      description="Start, stop, or inspect Jarvis-owned TencentDB and Ollama services.")
    ctx.register_command("jarvis", handler=_handle_command,
                         description="Manage Jarvis-owned TencentDB and Ollama services.", args_hint="<status|start|stop>")
    ctx.register_cli_command(
        name="start",
        help="Start a Hermes extension",
        setup_fn=_setup_jarvis_start,
        handler_fn=_handle_jarvis_start,
        description="Start the Jarvis plugin and its Jarvis-owned TencentDB/Ollama runtime.",
    )
    ctx.register_cli_command(
        name="stop",
        help="Stop a Hermes extension",
        setup_fn=_setup_jarvis_stop,
        handler_fn=_handle_jarvis_stop,
        description="Stop the Jarvis plugin's owned TencentDB/Ollama runtime without stopping Hermes.",
    )


_ORCHESTRATE_SCHEMA = {
    "type": "object", "properties": {"goal": {"type": "string"}, "profile_id": {"type": "string"}}, "required": ["goal"],
}
_RUNTIME_SCHEMA = {
    "type": "object", "properties": {"action": {"type": "string", "enum": ["status", "start", "stop"]}},
}
_RECORD_OUTCOME_SCHEMA = {
    "type": "object", "properties": {
        "goal": {"type": "string"}, "status": {"type": "string", "enum": ["success", "failed", "partial"]},
        "profile_id": {"type": "string"}, "session_id": {"type": "string"}, "strategy": {"type": "string"},
        "bots": {"type": "array", "items": {"type": "string"}}, "deliverable": {"type": "string"},
        "quality": {"type": "number"}, "evidence": {"type": "object"}, "lessons": {"type": "array", "items": {"type": "string"}},
    }, "required": ["goal"],
}


__all__ = ["JarvisPluginRuntime", "register"]
