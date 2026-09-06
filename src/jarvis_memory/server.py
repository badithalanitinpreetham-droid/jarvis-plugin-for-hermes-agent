import asyncio
import json
import logging
from typing import List

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from jarvis_memory.config import CONFIG
from jarvis_memory.core import JarvisEngine
from jarvis_memory.gateway_supervisor import GatewaySupervisor
from jarvis_memory.tencent_memory import TencentMemoryClient
from jarvis_memory.tools import AutonomousExecutor, WorkflowPlanner, WorkflowProgressRenderer
from jarvis_memory.workflow_store import WorkflowStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("jarvis-server")

app = Server("jarvis-memory")

_memory_client = TencentMemoryClient()  # reads TDAI_GATEWAY_URL / TDAI_API_KEY from config/env
engine = JarvisEngine(_memory_client)

_workflow_store = WorkflowStore(CONFIG.workflow_db_path)

# No local LLM wired here on purpose: planning is Hermes' job (it already has
# its own API key configured and is the agent actually doing the reasoning).
# jarvis_plan_workflow falls back to WorkflowPlanner's heuristic 2-step plan
# unless Hermes explicitly supplies a real plan via jarvis_start_workflow.
planner = WorkflowPlanner(llm_client=None, memory_engine=engine, store=_workflow_store)

autonomous_executor = AutonomousExecutor(
    memory_engine=engine,
    config={
        "auto_approve_confidence": CONFIG.auto_approve_confidence,
        "replan_max_retries": CONFIG.replan_max_retries,
        "step_timeout": CONFIG.step_timeout,
    },
    store=_workflow_store,
    planner=planner,
)

progress_renderer = WorkflowProgressRenderer()

# GATEWAY_START_CMD (env) — set this if jarvis-memory should own the Gateway
# subprocess (auto-start + auto-recovery). Leave unset if you're running the
# Gateway yourself (Docker, systemd, etc.); the supervisor then only monitors.
# jarvis-memory + TencentDB Gateway = one unit from Hermes' perspective.
_supervisor = GatewaySupervisor(
    _memory_client,
    autonomous_executor=autonomous_executor,
)


@app.list_tools()
async def list_tools() -> List[types.Tool]:
    return [
        # --- Memory ---
        types.Tool(
            name="jarvis_recall",
            description="Search profile memory (backed by TencentDB Agent Memory / MemoryCore) for facts, preferences, or corrections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string", "description": "The user/profile ID (e.g., 'coder', 'writer')"},
                    "query": {"type": "string", "description": "Natural language query"}
                },
                "required": ["profile_id", "query"]
            }
        ),
        types.Tool(
            name="jarvis_learn",
            description="Forward a session transcript into MemoryCore for automatic extraction (facts/corrections/skills). No local LLM call — MemoryCore's own pipeline does the distillation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string", "description": "The user/profile ID"},
                    "transcript": {"type": "string", "description": "Full conversation transcript"}
                },
                "required": ["profile_id", "transcript"]
            }
        ),
        types.Tool(
            name="jarvis_auto_learn",
            description="Lightweight per-turn incremental capture. Capture a single turn as it happens instead of waiting for the full session — ideal for long sessions that might be interrupted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string", "description": "The user/profile ID"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system"], "description": "Who said this turn"},
                    "content": {"type": "string", "description": "The turn content"},
                },
                "required": ["profile_id", "role", "content"]
            }
        ),

        # --- HTML editing ---
        types.Tool(
            name="jarvis_edit_html",
            description="Visually edit HTML using CSS selectors (Lemon AI style).",
            inputSchema={
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "The HTML content to edit"},
                    "selector": {"type": "string", "description": "CSS selector (e.g., '#id', '.class')"},
                    "action": {"type": "string", "enum": ["replace", "append", "remove"], "description": "Action to perform"},
                    "content": {"type": "string", "description": "New content (for replace/append)"}
                },
                "required": ["html", "selector", "action"]
            }
        ),

        # --- Workflow planning ---
        types.Tool(
            name="jarvis_plan_workflow",
            description="Break down a high-level goal into a step plan with confidence/risk per step. Falls back to a generic 2-step heuristic plan — Hermes is expected to supply a real plan for anything nontrivial.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "High-level goal to achieve"},
                    "profile_id": {"type": "string", "description": "The user/profile ID"},
                    "context": {"type": "string", "description": "Additional context or constraints"}
                },
                "required": ["goal", "profile_id"]
            }
        ),
        types.Tool(
            name="jarvis_show_plan",
            description="Render a plan as a visual Mermaid flowchart before execution starts — shows each step, confidence, risk, and approval requirements.",
            inputSchema={
                "type": "object",
                "properties": {
                    "plan": {"type": "object", "description": "The plan object to visualize"}
                },
                "required": ["plan"]
            }
        ),

        # --- Workflow execution ---
        types.Tool(
            name="jarvis_start_workflow",
            description="Register a plan and get back the first step to execute (or an awaiting_approval status). jarvis-memory does not execute steps itself — Hermes runs the step with its own tools, then calls jarvis_report_step_result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "Unique ID for this workflow"},
                    "plan": {"type": "object", "description": "The plan object from jarvis_plan_workflow (or your own)"},
                    "profile_id": {"type": "string", "description": "The user/profile ID"}
                },
                "required": ["workflow_id", "plan", "profile_id"]
            }
        ),
        types.Tool(
            name="jarvis_get_pending_workflows",
            description="Get a list of all active or stalled workflow IDs, including proactive crons or system recovery tasks. Use this periodically or after a restart.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="jarvis_get_next_step",
            description="Get the current pending/next step of a workflow without advancing it. Use after a restart to see where a workflow left off.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"}
                },
                "required": ["workflow_id"]
            }
        ),
        types.Tool(
            name="jarvis_report_step_result",
            description="Report the real outcome of a step Hermes just executed, and get the next step back. If the step failed and auto-replan is enabled, a new plan may be generated automatically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"},
                    "step_id": {"type": "integer", "description": "The step ID that was executed"},
                    "status": {"type": "string", "enum": ["success", "failed"], "description": "Real outcome of running the step"},
                    "output": {"type": "string", "description": "Result/output text on success"},
                    "error": {"type": "string", "description": "Error text on failure"}
                },
                "required": ["workflow_id", "step_id", "status"]
            }
        ),
        types.Tool(
            name="jarvis_approve_step",
            description="Approve a pending high-risk/low-confidence step, then get it back as ready_to_execute.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"},
                    "step_id": {"type": "integer", "description": "The step ID to approve"}
                },
                "required": ["workflow_id", "step_id"]
            }
        ),

        # --- Workflow monitoring ---
        types.Tool(
            name="jarvis_progress",
            description="Get a visual progress bar and status for a running workflow — shows completion percentage, step-by-step status, and replan count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"},
                    "format": {"type": "string", "enum": ["bar", "mermaid", "kanban"], "description": "Output format: 'bar' (ASCII progress), 'mermaid' (color-coded graph), 'kanban' (column board). Default: bar"}
                },
                "required": ["workflow_id"]
            }
        ),
        types.Tool(
            name="jarvis_workflow_status",
            description="Get the full current status of an active workflow including replan history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"}
                },
                "required": ["workflow_id"]
            }
        ),

        # --- Workflow lifecycle ---
        types.Tool(
            name="jarvis_replan_workflow",
            description="Manually trigger replanning for a workflow after a step failure. Use when auto-replan is not enabled or when you want to force a replan with specific context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID"},
                    "failed_step_id": {"type": "integer", "description": "The step ID that failed"},
                    "error": {"type": "string", "description": "The error that caused the failure"}
                },
                "required": ["workflow_id", "failed_step_id", "error"]
            }
        ),
        types.Tool(
            name="jarvis_cancel_workflow",
            description="Cancel an active workflow. Cannot cancel already-completed or failed workflows.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID to cancel"},
                    "reason": {"type": "string", "description": "Why the workflow is being cancelled"}
                },
                "required": ["workflow_id"]
            }
        ),
        types.Tool(
            name="jarvis_reflect_workflow",
            description="After a workflow finishes, distill what happened into a lesson and store it in memory — the reflect step of a plan/act/reflect/memory loop.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID to reflect on"}
                },
                "required": ["workflow_id"]
            }
        ),

        # --- OS Assistant & Telemetry ---
        types.Tool(
            name="jarvis_speak",
            description="Use the native macOS text-to-speech engine to speak out loud to the user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to speak out loud"}
                },
                "required": ["text"]
            }
        ),
        types.Tool(
            name="jarvis_monitor_operative",
            description="Get real-time system hardware telemetry (CPU, RAM, Disk, Battery).",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="jarvis_os_control",
            description="Control macOS hardware natively.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["set_volume", "mute", "unmute", "lock_screen", "play_pause_media"], "description": "The OS action to perform"},
                    "value": {"type": "string", "description": "Optional value for the action (e.g. '50' for set_volume)"}
                },
                "required": ["action"]
            }
        ),

        types.Tool(
            name="jarvis_list_pending_approvals",
            description="List every workflow currently paused awaiting approval, across all profiles.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="jarvis_schedule_goal",
            description="Schedule a workflow to automatically run in the background on a recurring timer (Proactivity).",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The goal for the scheduled workflow"},
                    "profile_id": {"type": "string", "description": "The profile ID to tie this to"},
                    "interval_seconds": {"type": "integer", "description": "How often to run this in seconds (e.g. 3600 for hourly)"}
                },
                "required": ["goal", "profile_id", "interval_seconds"]
            }
        ),
        types.Tool(
            name="jarvis_mark_tool_broken",
            description="Manually flag a tool as broken. The planner will automatically force a 'Tool Repair' step the next time someone uses it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The name of the broken tool"},
                    "reason": {"type": "string", "description": "Why it is broken"}
                },
                "required": ["tool_name", "reason"]
            }
        ),
        types.Tool(
            name="jarvis_mark_tool_fixed",
            description="Manually clear the broken status of a tool after it has been repaired.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The name of the fixed tool"}
                },
                "required": ["tool_name"]
            }
        ),
        types.Tool(
            name="jarvis_check_stalled",
            description="Find workflows where the current step has been running longer than the timeout without a result being reported. Returns a list of stalled workflows.",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout_seconds": {"type": "number", "description": "Override the default stall timeout (default: 1800s / 30 min)"}
                },
            }
        ),
        types.Tool(
            name="jarvis_health",
            description="Report MemoryCore Gateway reachability, circuit-breaker state, and supervisor status. Use for monitoring/alerting.",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="jarvis_self_evolve",
            description="Cross-workflow self-evolution analysis. Scans ALL completed workflows for a profile and returns aggregate patterns: which tools/approaches reliably work vs fail, completion rate, and actionable recommendations. Call periodically to understand strengths and weaknesses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string", "description": "The profile to analyze evolution for"}
                },
                "required": ["profile_id"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> List[types.TextContent]:
    try:
        # --- Memory ---
        if name == "jarvis_recall":
            _supervisor.note_profile_active(arguments["profile_id"])
            results = engine.search_memory(arguments["profile_id"], arguments["query"])
            return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "jarvis_learn":
            _supervisor.note_profile_active(arguments["profile_id"])
            result = engine.analyze_and_learn(arguments["profile_id"], arguments["transcript"])
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_auto_learn":
            _supervisor.note_profile_active(arguments["profile_id"])
            result = engine.auto_capture_turn(
                arguments["profile_id"],
                arguments["role"],
                arguments["content"],
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        # --- HTML editing ---
        elif name == "jarvis_edit_html":
            html_result = engine.edit_html(
                arguments["html"], arguments["selector"], arguments["action"], arguments.get("content", "")
            )
            return [types.TextContent(type="text", text=html_result)]

        # --- Workflow planning ---
        elif name == "jarvis_plan_workflow":
            plan = planner.create_plan(arguments["goal"], arguments["profile_id"], arguments.get("context", ""))
            return [types.TextContent(type="text", text=json.dumps(plan, indent=2))]

        elif name == "jarvis_show_plan":
            preview = progress_renderer.render_plan_preview(arguments["plan"])
            return [types.TextContent(type="text", text=preview)]

        # --- Workflow execution ---
        elif name == "jarvis_start_workflow":
            _supervisor.note_profile_active(arguments["profile_id"])
            result = autonomous_executor.start_workflow(
                arguments["workflow_id"], arguments["plan"], arguments["profile_id"]
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_get_pending_workflows":
            workflows = []
            all_wfs = dict(autonomous_executor.active_workflows)
            if autonomous_executor.store:
                all_wfs.update(autonomous_executor.store.load_all())
            for wid, wstate in all_wfs.items():
                if wstate.get("status") in ("running", "awaiting_approval"):
                    workflows.append({"id": wid, "goal": wstate.get("plan", {}).get("goal"), "status": wstate.get("status")})
            return [types.TextContent(type="text", text=json.dumps({"pending_workflows": workflows}, indent=2))]

        elif name == "jarvis_get_next_step":
            result = autonomous_executor.get_next_step(arguments["workflow_id"])
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_report_step_result":
            result = autonomous_executor.report_step_result(
                arguments["workflow_id"],
                arguments["step_id"],
                arguments["status"],
                arguments.get("output"),
                arguments.get("error"),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_approve_step":
            result = autonomous_executor.approve_step(arguments["workflow_id"], arguments["step_id"])
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        # --- Workflow monitoring ---
        elif name == "jarvis_progress":
            state = autonomous_executor.get_workflow_status(arguments["workflow_id"])
            if state is None:
                return [types.TextContent(type="text", text="Workflow not found")]

            fmt = arguments.get("format", "bar")
            wf_id = arguments["workflow_id"]
            if fmt == "mermaid":
                output = progress_renderer.render_mermaid_status(wf_id, state)
            elif fmt == "kanban":
                output = progress_renderer.render_kanban(wf_id, state)
            else:
                output = progress_renderer.render_progress_bar(wf_id, state)
            return [types.TextContent(type="text", text=output)]

        elif name == "jarvis_workflow_status":
            status = autonomous_executor.get_workflow_status(arguments["workflow_id"])
            if status:
                return [types.TextContent(type="text", text=json.dumps(status, indent=2))]
            return [types.TextContent(type="text", text="Workflow not found")]

        # --- Workflow lifecycle ---
        elif name == "jarvis_replan_workflow":
            wf_id = arguments["workflow_id"]
            state = autonomous_executor.get_workflow_status(wf_id)
            if state is None:
                return [types.TextContent(type="text", text=json.dumps({"error": "Workflow not found"}))]

            new_plan = planner.replan(
                original_plan=state["plan"],
                failed_step=arguments["failed_step_id"],
                error=arguments["error"],
                history=state.get("history", []),
            )
            
            if not planner._validate_plan(new_plan):
                return [types.TextContent(type="text", text=json.dumps({"error": "Replanning produced an invalid plan"}))]

            # Apply the new plan
            state["plan"] = new_plan
            state["next_index"] = 0
            state["approved_index"] = None
            state["completed_steps"] = []
            state["failed_steps"] = []
            state["archived_history"] = state.get("archived_history", []) + state.get("history", [])
            state["history"] = []
            state["replan_count"] = state.get("replan_count", 0) + 1
            state["replan_history"] = state.get("replan_history", [])
            state["replan_history"].append({
                "attempt": state["replan_count"],
                "failed_step_id": arguments["failed_step_id"],
                "error": arguments["error"],
                "result": "manual_replan",
                "new_steps_count": len(new_plan.get("steps", [])),
            })
            state["status"] = "running"
            autonomous_executor.active_workflows[wf_id] = state
            autonomous_executor._persist(wf_id)

            result = autonomous_executor.get_next_step(wf_id)
            result["replan_applied"] = True
            result["new_plan_steps"] = len(new_plan.get("steps", []))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_cancel_workflow":
            result = autonomous_executor.cancel_workflow(
                arguments["workflow_id"],
                arguments.get("reason", ""),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_reflect_workflow":
            result = autonomous_executor.reflect(arguments["workflow_id"])
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        # --- Operations ---
        elif name == "jarvis_list_pending_approvals":
            result = autonomous_executor.list_pending_approvals()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_check_stalled":
            timeout = arguments.get("timeout_seconds")
            result = autonomous_executor.check_stalled_workflows(timeout)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_health":
            result = {
                "gateway": _memory_client.status(),
                "supervisor": _supervisor.status(),
            }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_self_evolve":
            result = autonomous_executor.self_evolve(arguments["profile_id"])
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_schedule_goal":
            _workflow_store.add_trigger(
                goal=arguments["goal"],
                profile_id=arguments["profile_id"],
                interval_seconds=arguments["interval_seconds"]
            )
            result = {"status": "success", "message": f"Goal scheduled every {arguments['interval_seconds']} seconds."}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_mark_tool_broken":
            _workflow_store.mark_tool_broken(arguments["tool_name"], arguments["reason"])
            result = {"status": "success", "message": f"Tool '{arguments['tool_name']}' flagged as broken. Next plan using it will trigger an auto-repair phase."}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_mark_tool_fixed":
            _workflow_store.mark_tool_fixed(arguments["tool_name"])
            result = {"status": "success", "message": f"Tool '{arguments['tool_name']}' is now marked as fixed and will be used normally."}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        # --- OS Assistant & Telemetry ---
        elif name == "jarvis_speak":
            from .tools.os_assistant import OSAssistant
            result = OSAssistant.speak(arguments["text"])
            return [types.TextContent(type="text", text=result)]

        elif name == "jarvis_monitor_operative":
            from .tools.os_assistant import OSAssistant
            result = OSAssistant.get_telemetry()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jarvis_os_control":
            from .tools.os_assistant import OSAssistant
            result = OSAssistant.control_os(arguments["action"], arguments.get("value", ""))
            return [types.TextContent(type="text", text=result)]

        else:
            return [types.TextContent(type="text", text=f"Error: Tool '{name}' not found")]

    except KeyError as e:
        logger.warning("Tool '%s' called with missing argument: %s", name, e)
        return [types.TextContent(type="text", text=f"Error: Missing argument {e}")]
    except Exception as e:
        logger.exception("Tool '%s' raised an unhandled exception", name)
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


def main():
    logger.info("Starting Jarvis Memory MCP Server (TencentDB Agent Memory backend)...")
    _supervisor.start()
    try:
        asyncio.run(run())
    finally:
        _supervisor.stop()


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
