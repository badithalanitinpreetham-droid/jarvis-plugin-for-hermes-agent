"""Jarvis intelligence: routing, workforce scoring, deliverable detection and learning."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .experience_store import ExperienceStore


_SIMPLE_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|continue|go ahead|do it|done|next)[\s!?.:;,`'\"~()\[\]{}<>*&^%$#@!+=-]*$",
    re.IGNORECASE,
)
_COMPLEX_HINTS = (
    "build", "create", "develop", "research", "compare", "analyse", "analyze", "report",
    "documentary", "video", "youtube", "automate", "automation", "pipeline", "deploy", "publish",
    "monitor", "every day", "every week", "continuously", "multiple", "end-to-end", "workflow",
    "project", "production", "investigate", "implement", "migrate", "refactor",
)
_DELIVERABLE_HINTS = (
    "report", "document", "file", "spreadsheet", "presentation", "video", "script", "dataset",
    "codebase", "repository", "package", "plan", "proposal", "email", "post", "thumbnail", "artifact",
)


@dataclass(frozen=True)
class RoutingDecision:
    mode: str
    reason: str
    requires_kanban: bool
    deliverable: bool
    suggested_roles: List[str] = field(default_factory=list)
    confidence: float = 0.0


class JarvisIntelligence:
    """Pure decision layer. It recommends work organisation; Hermes performs execution."""

    def __init__(self, registry: Any = None, store: Optional[ExperienceStore] = None) -> None:
        self.registry = registry
        self.store = store or ExperienceStore()

    @staticmethod
    def classify(goal: str) -> RoutingDecision:
        text = str(goal or "").strip()
        lower = text.lower()
        if not text or _SIMPLE_RE.match(text):
            return RoutingDecision("simple", "Prompt is short/trivial", False, False, [], 0.98)
        hint_count = sum(1 for item in _COMPLEX_HINTS if item in lower)
        deliverable = any(item in lower for item in _DELIVERABLE_HINTS)
        long_goal = len(text) > 280
        numbered = len(re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+", text)) >= 2
        recurring = any(item in lower for item in ("every ", "daily", "weekly", "monthly", "scheduled"))
        complex = hint_count >= 2 or long_goal or numbered or recurring
        if not complex and deliverable:
            return RoutingDecision("moderate", "A concrete deliverable is requested", False, True, ["writer", "reviewer"], 0.84)
        if complex:
            roles = ["researcher", "analyst", "writer", "reviewer"] if deliverable else ["developer", "reviewer"]
            if recurring:
                roles.append("operator")
            return RoutingDecision("complex", "Goal has multiple steps, dependencies, recurrence or deliverables", True, deliverable, roles, 0.9)
        return RoutingDecision("moderate", "Goal needs more than a trivial response", False, deliverable, ["specialist"], 0.72)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {x for x in re.findall(r"[a-z0-9][a-z0-9+_.-]{2,}", text.lower())}

    def rank_bots(self, goal: str, bots: Sequence[Dict[str, Any]], roles: Iterable[str]) -> List[Dict[str, Any]]:
        goal_tokens = self._tokens(goal)
        role_tokens = self._tokens(" ".join(roles))
        scored: List[tuple[float, Dict[str, Any]]] = []
        for bot in bots:
            blob = " ".join(
                str(bot.get(key, ""))
                for key in ("name", "role", "description", "capabilities", "skills", "toolsets")
            )
            bot_tokens = self._tokens(blob)
            overlap = len(goal_tokens & bot_tokens)
            role_overlap = len(role_tokens & bot_tokens)
            performance = float(bot.get("performance", bot.get("success_rate", 0.0)) or 0.0)
            available = 0.1 if bot.get("available", bot.get("configured", True)) else -1.0
            score = overlap * 0.8 + role_overlap * 1.2 + performance * 2.0 + available
            scored.append((score, {**bot, "jarvis_score": round(score, 3)}))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", pair[1].get("name", "")))))
        return [bot for _, bot in scored]

    def context_for_goal(self, goal: str, *, profile_id: str = "", limit: int = 6) -> Dict[str, Any]:
        decision = self.classify(goal)
        recent = self.store.recent(goal, limit=limit)
        lessons: List[str] = []
        for row in recent:
            try:
                import json
                lessons.extend(json.loads(row.get("lessons") or "[]"))
            except Exception:
                continue
        bots: List[Dict[str, Any]] = []
        if self.registry is not None:
            try:
                bots = list(self.registry.list_bots())
            except Exception:
                bots = []
        ranked = self.rank_bots(goal, bots, decision.suggested_roles)
        return {
            "routing": decision.__dict__,
            "profile_id": profile_id,
            "recommended_bots": ranked[: max(1, min(len(ranked), 6))],
            "experience": recent[:limit],
            "lessons": lessons[:10],
        }

    def observe_outcome(self, *, goal: str, status: str, profile_id: str = "", session_id: str = "",
                        strategy: str = "", bots: Optional[Iterable[str]] = None,
                        deliverable: str = "", quality: Optional[float] = None,
                        evidence: Optional[Dict[str, Any]] = None,
                        lessons: Optional[Iterable[str]] = None) -> int:
        return self.store.record_outcome(
            goal=goal, status=status, profile_id=profile_id, session_id=session_id,
            strategy=strategy, bots=bots, deliverable=deliverable, quality=quality,
            evidence=evidence, lessons=lessons,
        )

    def evolve_policy(self, key: str, *, success: bool, proposal: str, confidence: float = 0.6) -> Dict[str, Any]:
        """Evidence-gated evolution record; callers decide when a proposal is safe to adopt."""
        return self.store.record_policy_result(key, success=success, value=proposal, confidence=confidence)

    def close(self) -> None:
        self.store.close()


__all__ = ["JarvisIntelligence", "RoutingDecision"]
