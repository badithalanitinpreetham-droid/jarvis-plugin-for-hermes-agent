"""Dynamic organisation design for Hermes Bots and temporary workers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping


@dataclass(frozen=True)
class AgentAssignment:
    """A role Jarvis wants Hermes to staff for a goal."""

    role: str
    purpose: str
    bot_preference: str = "existing_first"
    permanent: bool = True
    capabilities: List[str] = field(default_factory=list)
    selected_bot: str | None = None
    selected_bot_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "purpose": self.purpose,
            "bot_preference": self.bot_preference,
            "permanent": self.permanent,
            "capabilities": list(self.capabilities),
            "selected_bot": self.selected_bot,
            "selected_bot_reason": self.selected_bot_reason,
        }


@dataclass(frozen=True)
class OrganisationPlan:
    """The organisation Jarvis recommends; Hermes remains responsible for staffing."""

    mode: str
    summary: str
    assignments: List[AgentAssignment]
    temporary_agent_reasons: List[str]
    kanban_policy: Dict[str, Any]
    available_bot_count: int = 0
    unfilled_roles: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "summary": self.summary,
            "recommended_worker_count": len(self.assignments),
            "assignments": [item.as_dict() for item in self.assignments],
            "temporary_agent_reasons": list(self.temporary_agent_reasons),
            "kanban_policy": dict(self.kanban_policy),
            "available_bot_count": self.available_bot_count,
            "unfilled_roles": list(self.unfilled_roles),
        }


class OrganisationPlanner:
    """Deterministic organisation planner used before an optional LLM plan.

    Hermes owns the real Bot registry. Jarvis may receive a lightweight roster
    snapshot and use it to select existing specialists without creating or
    replacing agents itself.
    """

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

    @staticmethod
    def _normalise_roster(available_bots: Iterable[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
        roster: List[Dict[str, Any]] = []
        for raw in available_bots or []:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("id") or raw.get("name") or raw.get("profile_id") or "").strip()
            if not name:
                continue
            capabilities = raw.get("capabilities", raw.get("skills", []))
            if isinstance(capabilities, str):
                capabilities = [capabilities]
            if not isinstance(capabilities, Iterable):
                capabilities = []
            roster.append(
                {
                    "id": name,
                    "role": str(raw.get("role") or "").strip().lower(),
                    "capabilities": {str(item).strip().lower() for item in capabilities if str(item).strip()},
                    "description": str(raw.get("description") or raw.get("purpose") or "").strip().lower(),
                }
            )
        return roster

    @staticmethod
    def _select_bot(role: str, capabilities: List[str], roster: List[Dict[str, Any]], used: set[str]) -> tuple[str | None, str]:
        best_id: str | None = None
        best_score = 0
        required = {role.lower(), *(item.lower() for item in capabilities)}
        for bot in roster:
            bot_id = bot["id"]
            if bot_id in used:
                continue
            score = 0
            if bot["role"] == role.lower():
                score += 5
            score += len(required & bot["capabilities"]) * 2
            if role.lower() in bot["description"]:
                score += 2
            if score > best_score:
                best_score = score
                best_id = bot_id
        if best_id:
            return best_id, "Existing Hermes Bot matched by role/capability overlap."
        return None, "No unused permanent Hermes Bot matched strongly enough; Hermes may use a temporary worker."

    def design(
        self,
        goal: str,
        context: str = "",
        lessons: List[str] | None = None,
        available_bots: Iterable[Mapping[str, Any]] | None = None,
    ) -> OrganisationPlan:
        text = f"{goal}\n{context}".lower()
        lessons = lessons or []
        roster = self._normalise_roster(available_bots)
        used_bots: set[str] = set()
        assignments: List[AgentAssignment] = []

        for role, keywords, purpose, capabilities in self._ROLE_RULES:
            if any(keyword in text for keyword in keywords):
                selected_bot, reason = self._select_bot(role, capabilities, roster, used_bots)
                if selected_bot:
                    used_bots.add(selected_bot)
                assignments.append(
                    AgentAssignment(
                        role=role,
                        purpose=purpose,
                        capabilities=list(capabilities),
                        selected_bot=selected_bot,
                        selected_bot_reason=reason,
                    )
                )

        if not assignments:
            selected_bot, reason = self._select_bot("generalist", ["planning", "execution", "verification"], roster, used_bots)
            if selected_bot:
                used_bots.add(selected_bot)
            assignments.append(
                AgentAssignment(
                    role="generalist",
                    purpose="Own the goal end-to-end using the best available Hermes capabilities.",
                    capabilities=["planning", "execution", "verification"],
                    selected_bot=selected_bot,
                    selected_bot_reason=reason,
                )
            )

        unfilled_roles = [item.role for item in assignments if not item.selected_bot]

        complex_markers = (
            len(goal) > 180,
            len(assignments) >= 3,
            any(
                word in text
                for word in (
                    "multiple",
                    "parallel",
                    "large-scale",
                    "high volume",
                    "100",
                    "thousand",
                    "every day",
                )
            ),
            len(lessons) >= 4,
            bool(unfilled_roles) and len(assignments) > 1,
        )
        complex = sum(bool(item) for item in complex_markers) >= 2

        temporary_reasons: List[str] = []
        if unfilled_roles:
            temporary_reasons.append(
                "No suitable permanent Hermes Bot was supplied for: "
                + ", ".join(unfilled_roles)
                + ". Hermes can use a temporary specialist if the role is genuinely needed."
            )
        if complex:
            temporary_reasons.append(
                "Use temporary subagents for parallel research, specialist gaps, or "
                "high-volume subtasks; prefer existing permanent Bots first."
            )
        if len(assignments) >= 3:
            temporary_reasons.append(
                "A temporary verifier or fact-checker may be added when independent "
                "validation reduces risk."
            )

        if len(assignments) > 1:
            dependency_mode = "dependency_aware"
            parallelism = max(2, min(4, len(assignments)))
        else:
            dependency_mode = "simple"
            parallelism = 1

        kanban_policy = {
            "use_hermes_kanban": len(assignments) > 1 or complex,
            "create_tasks_from_roles": True,
            "dependency_mode": dependency_mode,
            "max_parallel_workers": parallelism,
            "review_gate_before_external_side_effects": True,
            "reuse_existing_bots_first": True,
            "temporary_agents": "only_when_parallelism_specialist_gap_or_independent_verification_justifies",
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
            available_bot_count=len(roster),
            unfilled_roles=unfilled_roles,
        )
