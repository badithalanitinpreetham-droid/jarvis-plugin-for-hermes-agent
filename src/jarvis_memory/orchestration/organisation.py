"""Dynamic organisation design for Hermes Bots and temporary workers."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Dict, Iterable, List, Mapping

from .registry import HermesRegistry


@dataclass(frozen=True)
class AgentAssignment:
    """A role Jarvis recommends Hermes staff for a goal."""

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
    """Deterministic first-pass organisation planner with live Hermes discovery.

    Hermes owns the actual Bot/profile system. Jarvis only reads a small, safe
    metadata projection and recommends staffing; it never creates or mutates Bots.
    """

    _ROLE_RULES = (
        ("researcher", ("research", "investigate", "survey", "compare", "literature", "market", "sources"),
         "Find and synthesise reliable source material.", ("web_research", "source_analysis")),
        ("writer", ("write", "script", "article", "report", "documentation", "content"),
         "Transform approved research into a clear deliverable.", ("writing", "editing")),
        ("developer", ("build", "develop", "code", "implement", "software", "app", "bug", "fix"),
         "Design, implement and test the technical solution.", ("coding", "testing")),
        ("reviewer", ("review", "verify", "validate", "audit", "quality", "check", "fact-check"),
         "Independently inspect outputs against success criteria.", ("verification", "quality_assurance")),
        ("publisher", ("publish", "youtube", "upload", "release", "post", "deploy"),
         "Prepare and publish the final deliverable after approval.", ("publishing", "release_management")),
        ("analyst", ("analyse", "analyze", "data", "metrics", "forecast", "evaluate"),
         "Turn raw information into structured analysis and decisions.", ("analysis", "data_processing")),
    )

    def __init__(self, registry: HermesRegistry | None = None, discovery_ttl: float = 5.0):
        self.registry = registry or HermesRegistry()
        self.discovery_ttl = max(0.0, float(discovery_ttl))
        self._last_discovery = 0.0
        self._cached_bots: List[Dict[str, Any]] = []

    def _get_bots(self, available_bots: Iterable[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
        if available_bots is not None:
            return self._normalise_roster(available_bots)
        now = time.monotonic()
        if now - self._last_discovery >= self.discovery_ttl:
            try:
                self._cached_bots = self._normalise_roster(self.registry.discover().get("bots", []))
            except Exception:
                self._cached_bots = []
            self._last_discovery = now
        return [dict(item) for item in self._cached_bots]

    @staticmethod
    def _normalise_roster(available_bots: Iterable[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
        roster: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in available_bots or []:
            if not isinstance(raw, Mapping):
                continue
            bot_id = str(raw.get("id") or raw.get("name") or raw.get("profile_id") or "").strip()
            if not bot_id or bot_id in seen:
                continue
            seen.add(bot_id)
            capabilities = raw.get("capabilities", raw.get("skills", []))
            if isinstance(capabilities, str):
                capabilities = [capabilities]
            if not isinstance(capabilities, Iterable) or isinstance(capabilities, (bytes, bytearray, Mapping)):
                capabilities = []
            roster.append({
                "id": bot_id,
                "role": str(raw.get("role") or "").strip().lower(),
                "capabilities": {str(item).strip().lower() for item in capabilities if str(item).strip()},
                "description": str(raw.get("description") or raw.get("purpose") or "").strip().lower(),
                "available": bool(raw.get("available", True)),
            })
        return roster

    @staticmethod
    def _contains_term(text: str, term: str) -> bool:
        pattern = r"(?<![\w-])" + re.escape(term.lower()) + r"(?![\w-])"
        return re.search(pattern, text) is not None

    @classmethod
    def _role_matches(cls, text: str, keywords: Iterable[str]) -> bool:
        return any(cls._contains_term(text, keyword) for keyword in keywords)

    @staticmethod
    def _description_overlap(required: set[str], description: str) -> int:
        tokens = set(re.findall(r"[a-z0-9_+-]+", description.lower()))
        return len(required & tokens)

    @classmethod
    def _select_bot(cls, role: str, capabilities: Iterable[str], roster: List[Dict[str, Any]], used: set[str]) -> tuple[str | None, str]:
        required = {role.lower(), *(item.lower() for item in capabilities)}
        best_id: str | None = None
        best_score = 0
        for bot in roster:
            bot_id = bot["id"]
            if bot_id in used or not bot.get("available", True):
                continue
            score = 0
            if bot.get("role") == role.lower():
                score += 8
            score += 3 * len(required & bot.get("capabilities", set()))
            score += 2 * cls._description_overlap(required, bot.get("description", ""))
            if score > best_score:
                best_id, best_score = bot_id, score
        if best_id:
            return best_id, "Existing Hermes Bot matched by role/capability metadata."
        return None, "No suitable permanent Hermes Bot is currently known for this role."

    def design(
        self,
        goal: str,
        context: str = "",
        lessons: List[str] | None = None,
        available_bots: Iterable[Mapping[str, Any]] | None = None,
    ) -> OrganisationPlan:
        goal = str(goal or "").strip()
        context = str(context or "").strip()
        if not goal:
            raise ValueError("goal is required")
        text = f"{goal}\n{context}".lower()
        lessons = list(lessons or [])
        roster = self._get_bots(available_bots)
        used_bots: set[str] = set()
        assignments: List[AgentAssignment] = []

        for role, keywords, purpose, capabilities in self._ROLE_RULES:
            if self._role_matches(text, keywords):
                selected_bot, reason = self._select_bot(role, capabilities, roster, used_bots)
                if selected_bot:
                    used_bots.add(selected_bot)
                assignments.append(AgentAssignment(
                    role=role,
                    purpose=purpose,
                    capabilities=list(capabilities),
                    selected_bot=selected_bot,
                    selected_bot_reason=reason,
                ))

        if not assignments:
            selected_bot, reason = self._select_bot("generalist", ("planning", "execution", "verification"), roster, used_bots)
            if selected_bot:
                used_bots.add(selected_bot)
            assignments.append(AgentAssignment(
                role="generalist",
                purpose="Own the goal end-to-end using the best available Hermes capabilities.",
                capabilities=["planning", "execution", "verification"],
                selected_bot=selected_bot,
                selected_bot_reason=reason,
            ))

        unfilled_roles = [item.role for item in assignments if not item.selected_bot]
        complexity_score = sum((
            len(goal) > 180,
            len(assignments) >= 3,
            any(term in text for term in ("multiple", "parallel", "large-scale", "high volume", "every day")),
            len(lessons) >= 4,
            bool(unfilled_roles) and len(assignments) > 1,
        ))
        complex = complexity_score >= 2
        temporary_reasons: List[str] = []
        if unfilled_roles:
            temporary_reasons.append(
                "Permanent Hermes coverage is missing for: " + ", ".join(unfilled_roles) + ". "
                "Use a temporary specialist only if the role is necessary."
            )
        if complex:
            temporary_reasons.append(
                "Temporary workers are justified only for parallelism, genuine specialist gaps, "
                "or independent verification; existing permanent Bots remain preferred."
            )

        multi = len(assignments) > 1
        return OrganisationPlan(
            mode="multi_agent" if multi else "single_agent",
            summary=(f"Use {len(assignments)} role(s), preferring existing Hermes Bots. "
                     "Hermes owns any temporary-worker creation and Kanban execution."),
            assignments=assignments,
            temporary_agent_reasons=temporary_reasons,
            kanban_policy={
                "use_hermes_kanban": multi or complex,
                "create_tasks_from_roles": multi or complex,
                "dependency_mode": "dependency_aware" if multi else "simple",
                "max_parallel_workers": max(1, min(4, len(assignments))) if multi else 1,
                "review_gate_before_external_side_effects": True,
                "reuse_existing_bots_first": True,
                "temporary_agents": "only_when_justified",
            },
            available_bot_count=len(roster),
            unfilled_roles=unfilled_roles,
        )
