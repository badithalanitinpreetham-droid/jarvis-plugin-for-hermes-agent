"""Dynamic organisation design for Hermes Bots and temporary workers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class AgentAssignment:
    """A role Jarvis wants Hermes to staff for a goal."""

    role: str
    purpose: str
    bot_preference: str = "existing_first"
    permanent: bool = True
    capabilities: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "purpose": self.purpose,
            "bot_preference": self.bot_preference,
            "permanent": self.permanent,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class OrganisationPlan:
    """The organisation Jarvis recommends; Hermes remains responsible for staffing."""

    mode: str
    summary: str
    assignments: List[AgentAssignment]
    temporary_agent_reasons: List[str]
    kanban_policy: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "summary": self.summary,
            "assignments": [item.as_dict() for item in self.assignments],
            "temporary_agent_reasons": list(self.temporary_agent_reasons),
            "kanban_policy": dict(self.kanban_policy),
        }


class OrganisationPlanner:
    """Deterministic organisation planner used before an optional LLM plan is built."""

    _ROLE_RULES = (
        (
            "researcher",
            ("research", "investigate", "survey", "compare", "literature", "market"),
            "Find and synthesise reliable source material.",
            ["web_research", "source_analysis"],
        ),
        (
            "writer",
            ("write", "script", "article", "report", "documentation", "content"),
            "Transform approved research into clear deliverables.",
            ["writing", "editing"],
        ),
        (
            "developer",
            ("build", "develop", "code", "implement", "software", "app", "bug", "fix"),
            "Design, implement and test the technical solution.",
            ["coding", "testing"],
        ),
        (
            "reviewer",
            ("review", "verify", "validate", "audit", "quality", "check"),
            "Independently inspect outputs against success criteria.",
            ["verification", "quality_assurance"],
        ),
        (
            "publisher",
            ("publish", "youtube", "upload", "release", "post", "deploy"),
            "Prepare and publish the final deliverable after approval.",
            ["publishing", "release_management"],
        ),
        (
            "analyst",
            ("analyse", "analyze", "data", "metrics", "forecast", "evaluate"),
            "Turn raw information into structured analysis and decisions.",
            ["analysis", "data_processing"],
        ),
    )

    def design(self, goal: str, context: str = "", lessons: List[str] | None = None) -> OrganisationPlan:
        text = f"{goal}\n{context}".lower()
        lessons = lessons or []
        assignments: List[AgentAssignment] = []

        for role, keywords, purpose, capabilities in self._ROLE_RULES:
            if any(keyword in text for keyword in keywords):
                assignments.append(
                    AgentAssignment(
                        role=role,
                        purpose=purpose,
                        capabilities=list(capabilities),
                    )
                )

        if not assignments:
            assignments.append(
                AgentAssignment(
                    role="generalist",
                    purpose="Own the goal end-to-end using the best available Hermes capabilities.",
                    capabilities=["planning", "execution", "verification"],
                )
            )

        complex_markers = (
            len(goal) > 180,
            len(assignments) >= 3,
            any(word in text for word in ("multiple", "parallel", "large-scale", "100", "thousand", "every day")),
            len(lessons) >= 4,
        )
        complex = sum(bool(item) for item in complex_markers) >= 2

        temporary_reasons: List[str] = []
        if complex:
            temporary_reasons.append(
                "Use temporary subagents for parallel research, specialist gaps, or high-volume subtasks; prefer existing permanent Bots first."
            )
        if len(assignments) >= 3:
            temporary_reasons.append(
                "A temporary verifier or fact-checker may be added when independent validation reduces risk."
            )

        if len(assignments) > 1:
            flow = "dependency_aware"
            parallelism = max(2, min(4, len(assignments)))
        else:
            flow = "simple"
            parallelism = 1

        kanban_policy = {
            "use_hermes_kanban": len(assignments) > 1 or complex,
            "create_tasks_from_roles": True,
            "dependency_mode": flow,
            "max_parallel_workers": parallelism,
            "review_gate_before_external_side_effects": True,
            "reuse_existing_bots_first": True,
        }

        mode = "multi_agent" if len(assignments) > 1 else "single_agent"
        summary = (
            f"Staff {len(assignments)} role(s) using existing Hermes Bots first; "
            "create temporary workers only when parallelism, specialist coverage, "
            "or independent verification justifies them."
        )
        return OrganisationPlan(
            mode=mode,
            summary=summary,
            assignments=assignments,
            temporary_agent_reasons=temporary_reasons,
            kanban_policy=kanban_policy,
        )
