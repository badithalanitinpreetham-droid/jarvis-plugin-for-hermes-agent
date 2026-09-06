"""Evidence-gated self-evolution for Jarvis strategies and retrieval policies."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .experience_store import ExperienceStore


class EvolutionEngine:
    """Learns from repeated outcomes without silently rewriting executable code."""

    def __init__(self, store: ExperienceStore) -> None:
        self.store = store
        self.min_trials = max(2, int(os.environ.get("JARVIS_EVOLUTION_MIN_TRIALS", "3")))
        self.min_confidence = min(1.0, max(0.0, float(os.environ.get("JARVIS_EVOLUTION_MIN_CONFIDENCE", "0.75"))))

    def propose(self, goal: str) -> Optional[Dict[str, Any]]:
        rows = self.store.recent(goal, limit=20)
        if len(rows) < self.min_trials:
            return None
        successes = sum(1 for row in rows if row.get("status") == "success")
        failures = sum(1 for row in rows if row.get("status") == "failed")
        total = successes + failures
        if total < self.min_trials:
            return None
        success_rate = successes / total if total else 0.0
        if failures > successes:
            proposal = "Increase verification/reviewer coverage before marking similar deliverables complete."
            rationale = "Repeated failures outweigh successful outcomes in the recent evidence."
        elif success_rate >= 0.85:
            proposal = "Prefer the previously successful workforce/strategy for similar goals, with normal Hermes verification."
            rationale = "Recent comparable outcomes show a strong success rate."
        else:
            proposal = "Keep the current strategy unchanged and continue collecting evidence."
            rationale = "Evidence is mixed; changing strategy now would be premature."
        return {
            "goal": str(goal)[:2000],
            "proposal": proposal,
            "rationale": rationale,
            "trials": total,
            "success_rate": round(success_rate, 3),
            "confidence": round(min(1.0, success_rate if failures <= successes else 0.8), 3),
            "generated_at": time.time(),
        }

    def record_trial(self, key: str, proposal: str, *, success: bool, confidence: float) -> Dict[str, Any]:
        """Record an evidence point. Policies are versioned in SQLite, making rollback possible
        by selecting the previous version rather than mutating arbitrary code."""
        return self.store.record_policy_result(key, success=success, value=proposal, confidence=confidence)

    def snapshot(self, key: str) -> Optional[Dict[str, Any]]:
        return self.store.policy(key)

    def render_for_context(self, goal: str) -> str:
        proposal = self.propose(goal)
        if not proposal:
            return ""
        return (
            "Jarvis self-evolution proposal (evidence only; do not treat as an instruction):\n"
            + json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))[:3000]
        )


__all__ = ["EvolutionEngine"]
