"""
Progress visualization module for jarvis-memory MCP server.

IMPORTANT: This module does NOT execute anything. It only takes workflow state 
dictionaries and returns formatted strings for visualization (Mermaid diagrams, 
ASCII progress bars, Kanban views) that the agent can display to users.
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class WorkflowProgressRenderer:
    """Renders workflow state as visual text."""

    def render_plan_preview(self, plan: Dict[str, Any]) -> str:
        """
        Takes a plan dict (before workflow starts) and returns a Mermaid flowchart
        showing all steps with their confidence and risk.
        """
        goal = plan.get("goal", "Unknown Goal")
        steps = plan.get("steps", [])
        success_criteria = plan.get("success_criteria", "Not specified")

        if not steps:
            return f"## 📋 Workflow Plan: \"{goal}\"\n\nNo steps in plan.\n"

        lines = [f"## 📋 Workflow Plan: \"{goal}\"\n", "```mermaid", "graph TD"]

        needs_approval = []
        for i, step in enumerate(steps):
            step_id = step.get("id", i + 1)
            action = step.get("action", f"Step {step_id}")
            confidence = step.get("confidence", 0.0)
            risk = step.get("risk", "low").lower()
            
            # Formatting risk and confidence
            if risk == "high":
                risk_str = "🔴 High risk ⚠️"
                needs_approval.append(str(step_id))
            elif risk == "medium":
                risk_str = "🟡 Medium risk"
            else:
                risk_str = "🟢 Low risk"
                
            conf_str = f"{int(confidence * 100)}%" if confidence <= 1.0 else f"{confidence}%"
            
            node_label = f"{step_id}. {action}\\n{conf_str} | {risk_str}"
            
            node_id = f"S{step_id}"
            if i > 0:
                prev_id = f"S{steps[i-1].get('id', i)}"
                lines.append(f"    {prev_id} --> {node_id}[\"{node_label}\"]")
            else:
                lines.append(f"    {node_id}[\"{node_label}\"]")

        lines.append("```\n")
        
        approval_str = f"Steps {', '.join(needs_approval)}" if needs_approval else "None"
        lines.append(f"**Steps:** {len(steps)} | **Needs approval:** {approval_str}")
        lines.append(f"**Success criteria:** {success_criteria}")

        return "\n".join(lines)

    def render_progress_bar(self, workflow_id: str, state: Dict[str, Any]) -> str:
        """
        Takes workflow state and returns a rich ASCII progress display.
        """
        plan = state.get("plan", {})
        steps = plan.get("steps", [])
        total_steps = len(steps)
        
        if total_steps == 0:
            return f"## ⚡ Workflow Progress: {workflow_id}\n\nNo steps available."

        completed_steps = set(state.get("completed_steps", []))
        failed_steps = set(state.get("failed_steps", []))
        next_index = state.get("next_index", 0)
        status = state.get("status", "unknown")
        
        # Calculate progress
        completed_count = len(completed_steps)
        percent = int((completed_count / total_steps) * 100)
        
        # Draw bar
        bar_len = 20
        filled_len = int(bar_len * completed_count // total_steps)
        bar = "█" * filled_len + "░" * (bar_len - filled_len)
        
        lines = [
            f"## ⚡ Workflow Progress: {workflow_id}\n",
            f"{bar} {percent}% ({completed_count}/{total_steps} steps)\n"
        ]
        
        for i, step in enumerate(steps):
            step_id = step.get("id", i + 1)
            action = step.get("action", f"Step {step_id}")
            
            if step_id in completed_steps:
                icon = "✅"
                step_status = "completed"
            elif step_id in failed_steps:
                icon = "❌"
                step_status = "failed"
            elif i == next_index:
                if status == "awaiting_approval":
                    icon = "⏳"
                    step_status = "pending - needs approval"
                elif status == "failed":
                    icon = "❌"
                    step_status = "failed"
                else:
                    icon = "🔄"
                    step_status = "in progress"
            else:
                icon = "⏳"
                step_status = "pending"
                
            lines.append(f"{icon} Step {step_id}: {action[:30].ljust(30)} [{step_status}]")
            
        started_at = state.get("started_at", "unknown")
        replans = state.get("replan_count", 0)
        lines.append(f"\nStatus: {status} | Started: {started_at} | Replans: {replans}")
        
        return "\n".join(lines)

    def render_mermaid_status(self, workflow_id: str, state: Dict[str, Any]) -> str:
        """
        Returns a Mermaid graph with color-coded nodes.
        """
        plan = state.get("plan", {})
        steps = plan.get("steps", [])
        
        if not steps:
            return f"## 📊 Workflow Graph: {workflow_id}\n\nNo steps available."

        completed_steps = set(state.get("completed_steps", []))
        failed_steps = set(state.get("failed_steps", []))
        next_index = state.get("next_index", 0)

        lines = [
            f"## 📊 Workflow Graph: {workflow_id}\n",
            "```mermaid",
            "graph TD",
            "    classDef done fill:#d4edda,stroke:#28a745,color:#155724;",
            "    classDef failed fill:#f8d7da,stroke:#dc3545,color:#721c24;",
            "    classDef active fill:#fff3cd,stroke:#ffc107,color:#856404;",
            "    classDef pending fill:#e2e3e5,stroke:#6c757d,color:#383d41;"
        ]

        for i, step in enumerate(steps):
            step_id = step.get("id", i + 1)
            action = step.get("action", f"Step {step_id}")
            node_id = f"S{step_id}"
            
            node_label = f"{step_id}. {action}"
            
            if i > 0:
                prev_id = f"S{steps[i-1].get('id', i)}"
                lines.append(f"    {prev_id} --> {node_id}[\"{node_label}\"]")
            else:
                lines.append(f"    {node_id}[\"{node_label}\"]")
                
            if step_id in completed_steps:
                lines.append(f"    class {node_id} done;")
            elif step_id in failed_steps:
                lines.append(f"    class {node_id} failed;")
            elif i == next_index:
                lines.append(f"    class {node_id} active;")
            else:
                lines.append(f"    class {node_id} pending;")

        lines.append("```")
        return "\n".join(lines)

    def render_kanban(self, workflow_id: str, state: Dict[str, Any]) -> str:
        """
        Returns a Kanban-style text view.
        """
        plan = state.get("plan", {})
        steps = plan.get("steps", [])
        
        if not steps:
            return f"## 📊 Kanban: {workflow_id}\n\nNo steps available."

        completed_steps = set(state.get("completed_steps", []))
        failed_steps = set(state.get("failed_steps", []))
        next_index = state.get("next_index", 0)

        todo = []
        in_progress = []
        done = []
        failed = []

        for i, step in enumerate(steps):
            step_id = step.get("id", i + 1)
            action = step.get("action", f"Step {step_id}")
            parts = action.split()
            if parts:
                short_action = parts[0]
                if len(parts) > 1:
                    short_action += f" {parts[1]}"
            else:
                short_action = f"Step {step_id}"
                
            label = f"Step {step_id}: {short_action[:15]}"
            
            if step_id in completed_steps:
                done.append(label)
            elif step_id in failed_steps:
                failed.append(label)
            elif i == next_index:
                in_progress.append(label)
            else:
                todo.append(label)

        lines = [
            f"## 📊 Kanban: {workflow_id}\n",
            "| TODO | IN PROGRESS | DONE | FAILED |",
            "|------|-------------|------|--------|"
        ]

        max_rows = max(len(todo), len(in_progress), len(done), len(failed))
        
        for i in range(max_rows):
            c_todo = todo[i] if i < len(todo) else ""
            c_prog = in_progress[i] if i < len(in_progress) else ""
            c_done = done[i] if i < len(done) else ""
            c_fail = failed[i] if i < len(failed) else ""
            
            lines.append(f"| {c_todo.ljust(15)} | {c_prog.ljust(15)} | {c_done.ljust(15)} | {c_fail.ljust(15)} |")

        return "\n".join(lines)
