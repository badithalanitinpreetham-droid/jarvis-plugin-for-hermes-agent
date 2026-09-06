"""Tests for Jarvis organisation design and Hermes context packaging."""
from __future__ import annotations

import unittest

from jarvis_memory.orchestration.context import build_context_packet
from jarvis_memory.orchestration.experience import summarize_experience
from jarvis_memory.orchestration.organisation import OrganisationPlanner
from jarvis_memory.tools.planner import WorkflowPlanner, validate_plan


class TestOrganisationPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = OrganisationPlanner()

    def test_simple_goal_uses_a_single_worker(self):
        plan = self.planner.design("Summarise this document")
        self.assertEqual(plan.mode, "single_agent")
        self.assertEqual(len(plan.assignments), 1)
        self.assertEqual(plan.assignments[0].role, "generalist")

    def test_research_goal_selects_researcher(self):
        plan = self.planner.design("Research and compare AI video generators")
        roles = {item.role for item in plan.assignments}
        self.assertIn("researcher", roles)

    def test_complex_content_goal_recommends_multiple_roles(self):
        plan = self.planner.design(
            "Research, write, create and publish a large-scale YouTube content series "
            "with fact checking and daily production."
        )
        roles = {item.role for item in plan.assignments}
        self.assertGreaterEqual(len(roles), 3)
        self.assertTrue(plan.temporary_agent_reasons)
        self.assertTrue(plan.kanban_policy["use_hermes_kanban"])

    def test_existing_bots_are_preferred(self):
        plan = self.planner.design("Build a software application")
        self.assertTrue(all(item.bot_preference == "existing_first" for item in plan.assignments))
        self.assertTrue(plan.kanban_policy["reuse_existing_bots_first"])

    def test_roster_selects_matching_existing_bot(self):
        plan = self.planner.design(
            "Research competitors",
            available_bots=[
                {"id": "writer_bot", "role": "writer", "capabilities": ["writing"]},
                {"id": "research_bot", "role": "researcher", "capabilities": ["web_research"]},
            ],
        )
        researcher = next(item for item in plan.assignments if item.role == "researcher")
        self.assertEqual(researcher.selected_bot, "research_bot")
        self.assertEqual(plan.available_bot_count, 2)
        self.assertFalse(plan.unfilled_roles)

    def test_roster_leaves_unmatched_role_for_hermes(self):
        plan = self.planner.design(
            "Research and publish a report",
            available_bots=[{"id": "research_bot", "role": "researcher", "capabilities": ["web_research"]}],
        )
        self.assertEqual(
            next(item for item in plan.assignments if item.role == "researcher").selected_bot,
            "research_bot",
        )
        self.assertIn("writer", plan.unfilled_roles)
        self.assertIn("publisher", plan.unfilled_roles)


class TestWorkflowPlanner(unittest.TestCase):
    def test_context_json_can_supply_hermes_roster(self):
        planner = WorkflowPlanner(llm_client=None, memory_engine=None, store=None)
        plan = planner.create_plan(
            "Research and write a report",
            "user",
            context='{"working_context":"Public sources only", "available_bots":[{"id":"research_bot","role":"researcher","capabilities":["web_research"]},{"id":"writer_bot","role":"writer","capabilities":["writing"]}]}',
        )
        self.assertEqual(plan["context_packet"]["working_context"], "Public sources only")
        self.assertEqual(plan["organisation"]["available_bot_count"], 2)
        self.assertGreaterEqual(plan["estimated_steps"], 2)
        modes = {step.get("execution_mode") for step in plan["steps"]}
        self.assertIn("parallel", modes)

    def test_fallback_plan_keeps_research_and_analysis_parallel(self):
        planner = WorkflowPlanner(llm_client=None, memory_engine=None, store=None)
        plan = planner.create_plan("Research market data and analyse metrics", "user")
        steps = {step["parameters"]["role"]: step for step in plan["steps"]}
        self.assertEqual(steps["researcher"]["execution_mode"], "parallel")
        self.assertEqual(steps["analyst"]["execution_mode"], "parallel")

    def test_validate_plan_rejects_unknown_dependency_and_cycle(self):
        unknown = {
            "steps": [
                {"id": 1, "action": "A", "confidence": 1, "risk": "low", "depends_on": [99]}
            ]
        }
        self.assertIsNotNone(validate_plan(unknown))

        cycle = {
            "steps": [
                {"id": 1, "action": "A", "confidence": 1, "risk": "low", "depends_on": [2]},
                {"id": 2, "action": "B", "confidence": 1, "risk": "low", "depends_on": [1]},
            ]
        }
        self.assertIn("dependency cycle", validate_plan(cycle))


class TestExperienceSummary(unittest.TestCase):
    def test_summarises_successes_and_failures(self):
        summary = summarize_experience(
            [
                {"tool": "web", "status": "success"},
                {"tool": "web", "status": "success"},
                {"tool": "youtube", "status": "failed"},
            ],
            ["Use verified sources", "Use verified sources"],
        )
        self.assertEqual(summary.successful_tools, ["web"])
        self.assertEqual(summary.failed_tools, ["youtube"])
        self.assertEqual(summary.lessons, ["Use verified sources"])


class TestContextPacket(unittest.TestCase):
    def test_packet_contains_goal_roles_lessons_and_rules(self):
        organisation = OrganisationPlanner().design("Research and write a report").as_dict()
        packet = build_context_packet(
            goal="Research and write a report",
            profile_id="researcher",
            organisation=organisation,
            lessons=["Verify sources before publishing"],
            context="Use only public information",
        )
        self.assertEqual(packet["goal"], "Research and write a report")
        self.assertIn("researcher", packet["profile_id"])
        self.assertIn("Verify sources before publishing", packet["operational_lessons"])
        self.assertIn("Use existing Hermes Bots before creating temporary agents.", packet["rules"])


if __name__ == "__main__":
    unittest.main()
