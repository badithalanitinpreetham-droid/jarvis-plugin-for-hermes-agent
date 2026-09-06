"""Dynamic organisation design for Hermes Bots and temporary workers."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Dict, Iterable, List, Mapping

from .registry import HermesRegistry, get_default_registry


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
        return {"role": self.role, "purpose": self.purpose, "bot_preference": self.bot_preference,
                "permanent": self.permanent, "capabilities": list(self.capabilities),
                "selected_bot": self.selected_bot, "selected_bot_reason": self.selected_bot_reason}


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
        return {"mode": self.mode, "summary": self.summary,
                "recommended_worker_count": len(self.assignments),
                "assignments": [item.as_dict() for item in self.assignments],
                "temporary_agent_reasons": list(self.temporary_agent_reasons),
                "kanban_policy": dict(self.kanban_policy), "available_bot_count": self.available_bot_count,
                "unfilled_roles": list(self.unfilled_roles)}


class OrganisationPlanner:
    """Plan work against the current Hermes Bot/profile roster without owning it."""

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
        self.registry = registry or get_default_registry()
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
        roster, seen = [], set()
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
            roster.append({"id": bot_id, "role": str(raw.get("role") or "").strip().lower(),
                           "capabilities": {str(item).strip().lower() for item in capabilities if str(item).strip()},
                           "description": str(raw.get("description") or raw.get("purpose") or "").strip().lower(),
                           "available": bool(raw.get("available", True))})
        return roster

    @staticmethod
    def _contains_term(text: str, term: str) -> bool:
        return re.search(r"(?<![\w-])" + re.escape(term.lower()) + r"(?![\w-])", text) is not None

    @classmethod
    def _role_matches(cls, text: str, keywords: Iterable[str]) -> bool:
        return any(cls._contains_term(text, keyword) for keyword in keywords)

    @staticmethod
    def _description_overlap(required: set[str], description: str) -> int:
        return len(required & set(re.findall(r"[a-z0-9_+-]+", description.lower())))

    @classmethod
    def _select_bot(cls, role: str, capabilities: Iterable[str], roster: List[Dict[str, Any]], used: set[str]) -> tuple[str | None, str]:
        required = {role.lower(), *(item.lower() for item in capabilities)}
        best_id, best_score = None, 0
        for bot in roster:
            if bot["id"] in used or not bot.get("available", True):
                continue
            score = (8 if bot.get("role") == role.lower() else 0)
            score += 3 * len(required & bot.get("capabilities", set()))
            score += 2 * cls._description_overlap(required, bot.get("description", ""))
            if score > best_score:
                best_id, best_score = bot["id"], score
        return (best_id, "Existing Hermes Bot matched by role/capability metadata.") if best_id else (None, "No suitable permanent Hermes Bot is currently known for this role.")

    def design(self, goal: str, context: str = "", lessons: List[str] | None = None,
               available_bots: Iterable[Mapping[str, Any]] | None = None) -> OrganisationPlan:
        goal, context = str(goal or "").strip(), str(context or "").strip()
        if not goal:
            raise ValueError("goal is required")
        text, lessons = f"{goal}\n{context}".lower(), list(lessons or [])
        roster, used, assignments = self._get_bots(available_bots), set(), []
        for role, keywords, purpose, capabilities in self._ROLE_RULES:
            if self._role_matches(text, keywords):
                bot, reason = self._select_bot(role, capabilities, roster, used)
                if bot:
                    used.add(bot)
                assignments.append(AgentAssignment(role=role, purpose=purpose, capabilities=list(capabilities), selected_bot=bot, selected_bot_reason=reason))
        if not assignments:
            bot, reason = self._select_bot("generalist", ("planning", "execution", "verification"), roster, used)
            assignments.append(AgentAssignment(role="generalist", purpose="Own the goal end-to-end using the best available Hermes capabilities.", capabilities=["planning", "execution", "verification"], selected_bot=bot, selected_bot_reason=reason))
        unfilled = [item.role for item in assignments if not item.selected_bot]
        complexity = sum((len(goal) > 180, len(assignments) >= 3,
                          any(x in text for x in ("multiple", "parallel", "large-scale", "high volume", "every day")),
                          len(lessons) >= 4, bool(unfilled) and len(assignments) > 1)) >= 2
        reasons = []
        if unfilled:
            reasons.append("Permanent Hermes coverage is missing for: " + ", ".join(unfilled) + ". Use a temporary specialist only if necessary.")
        if complexity:
            reasons.append("Temporary workers are justified only for parallelism, genuine specialist gaps, or independent verification.")
        multi = len(assignments) > 1
        return OrganisationPlan("multi_agent" if multi else "single_agent",
            f"Use {len(assignments)} role(s), preferring existing Hermes Bots. Hermes owns temporary workers and Kanban execution.",
            assignments, reasons,
            {"use_hermes_kanban": multi or complexity, "create_tasks_from_roles": multi or complexity,
             "dependency_mode": "dependency_aware" if multi else "simple",
             "max_parallel_workers": max(1, min(4, len(assignments))) if multi else 1,
             "review_gate_before_external_side_effects": True, "reuse_existing_bots_first": True,
             "temporary_agents": "only_when_justified"}, len(roster), unfilled)
