"""Jarvis orchestration primitives.

Jarvis does not replace Hermes' tools, Bots, subagents or Kanban. These
modules decide how those existing Hermes capabilities should be organised
for a user goal and what bounded context should be supplied to workers.
"""

from .context import build_context_packet
from .experience import ExperienceSummary, summarize_experience
from .organisation import AgentAssignment, OrganisationPlanner, OrganisationPlan
from .registry import HermesRegistry

__all__ = [
    "AgentAssignment",
    "ExperienceSummary",
    "HermesRegistry",
    "OrganisationPlan",
    "OrganisationPlanner",
    "build_context_packet",
    "summarize_experience",
]
