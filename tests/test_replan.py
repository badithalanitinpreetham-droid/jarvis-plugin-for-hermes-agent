"""
Tests for WorkflowPlanner replanning and AutonomousExecutor auto-replan.

Run: python3 -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest

from jarvis_memory.tools.autonomous import AutonomousExecutor, validate_plan
from jarvis_memory.tools.planner import WorkflowPlanner
from jarvis_memory.workflow_store import WorkflowStore


def make_plan(steps):
    return {"goal": "test goal", "steps": steps}


def high_conf_step(step_id, action="do thing"):
    return {"id": step_id, "action": action, "confidence": 0.95, "risk": "low"}


class MockMemoryEngine:
    """Minimal mock that satisfies the add_memory / search_memory interface."""
    def __init__(self):
        self.memories = []

    def add_memory(self, user_id, text, metadata=None):
        self.memories.append({"user_id": user_id, "text": text, "metadata": metadata})
        return {"status": "success"}

    def search_memory(self, user_id, query, limit=5):
        return []


class TestReplanStructure(unittest.TestCase):
    def setUp(self):
        self.planner = WorkflowPlanner()
        self.plan = make_plan([high_conf_step(1, "do thing"), high_conf_step(2, "do next thing")])

    def test_replan_structure(self):
        new_plan = self.planner.replan(self.plan, 1, "failed")
        self.assertIsNone(validate_plan(new_plan))
        self.assertIn("steps", new_plan)
        self.assertTrue(len(new_plan["steps"]) >= 1)


class TestReplanPreservesRemaining(unittest.TestCase):
    def setUp(self):
        self.planner = WorkflowPlanner()
        self.plan = make_plan([high_conf_step(1, "do thing"), high_conf_step(2, "do next thing")])

    def test_replan_preserves_remaining(self):
        new_plan = self.planner.replan(self.plan, 1, "failed")
        # Second step should be preserved (may have re-numbered id)
        remaining_actions = [s["action"] for s in new_plan["steps"]]
        self.assertTrue(any("do next thing" in a for a in remaining_actions))


class TestReplanFailedStepRetry(unittest.TestCase):
    def setUp(self):
        self.planner = WorkflowPlanner()
        self.plan = make_plan([high_conf_step(1, "do thing"), high_conf_step(2, "do next thing")])

    def test_replan_failed_step_retry(self):
        new_plan = self.planner.replan(self.plan, 1, "failed")
        # First step should be a retry with alternative approach
        self.assertEqual(new_plan["steps"][0].get("approach"), "alternative")


class StoreBackedTestCase(unittest.TestCase):
    """Base class: gives each test its own temp SQLite file + a ready executor."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "workflows.db")
        self.store = WorkflowStore(self.db_path)
        self.memory_engine = MockMemoryEngine()

        class FakePlanner:
            def replan(self, original_plan, failed_step, error, history=None):
                new_steps = []
                for s in original_plan.get("steps", []):
                    if s["id"] == failed_step:
                        new_steps.append({
                            "id": s["id"], "action": s["action"] + " retry",
                            "confidence": 0.9, "risk": "low", "approach": "alternative",
                        })
                    elif s["id"] > failed_step:
                        new_steps.append(s)
                if not new_steps:
                    new_steps.append({
                        "id": 1, "action": "recovery step",
                        "confidence": 0.5, "risk": "medium", "approach": "alternative",
                    })
                return {"goal": original_plan.get("goal"), "steps": new_steps}

        self.executor = AutonomousExecutor(
            memory_engine=self.memory_engine,
            config={"auto_approve_confidence": 0.85, "replan_max_retries": 3},
            store=self.store,
            planner=FakePlanner(),
        )

    def tearDown(self):
        self.store.close()
        self._tmpdir.cleanup()


class TestAutoReplan(StoreBackedTestCase):
    def test_auto_replan(self):
        plan = make_plan([high_conf_step(1), high_conf_step(2)])
        self.executor.start_workflow("wf_replan_1", plan, "profile-a")

        result = self.executor.report_step_result("wf_replan_1", 1, "failed", error="boom")

        # After replan, should get back a ready_to_execute or awaiting_approval
        self.assertIn(result.get("status"), ("ready_to_execute", "awaiting_approval"))
        status = self.executor.get_workflow_status("wf_replan_1")
        self.assertEqual(status["replan_count"], 1)


class TestReplanCap(StoreBackedTestCase):
    def test_replan_cap(self):
        plan = make_plan([high_conf_step(1), high_conf_step(2)])
        self.executor.start_workflow("wf_replan_2", plan, "profile-a")

        # Each replan produces a new plan with a retry step, which we then
        # fail again. After 3 replans the cap is reached.
        for i in range(3):
            state = self.executor.get_workflow_status("wf_replan_2")
            steps = state["plan"]["steps"]
            current_step_id = steps[state["next_index"]]["id"]
            self.executor.report_step_result("wf_replan_2", current_step_id, "failed", error=f"boom {i}")

        # 4th failure — cap reached, no more replans
        state = self.executor.get_workflow_status("wf_replan_2")
        self.assertEqual(state["replan_count"], 3)


class TestCancelWorkflow(StoreBackedTestCase):
    def test_cancel_workflow(self):
        plan = make_plan([high_conf_step(1)])
        self.executor.start_workflow("wf_cancel", plan, "profile-a")
        result = self.executor.cancel_workflow("wf_cancel", "user requested")
        self.assertEqual(result["status"], "cancelled")

        status = self.executor.get_workflow_status("wf_cancel")
        self.assertEqual(status["status"], "cancelled")


class TestStalledDetection(StoreBackedTestCase):
    def test_stalled_detection(self):
        plan = make_plan([high_conf_step(1)])
        self.executor.start_workflow("wf_stall", plan, "profile-a")

        # Manually set step_started_at to an old time
        state = self.executor.active_workflows["wf_stall"]
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        state["step_started_at"] = old_time
        state["status"] = "running"
        self.executor._persist("wf_stall")

        stalled = self.executor.check_stalled_workflows(timeout_seconds=1800)
        stalled_ids = [s["workflow_id"] for s in stalled]
        self.assertIn("wf_stall", stalled_ids)


class TestCancelNonexistent(StoreBackedTestCase):
    def test_cancel_nonexistent(self):
        result = self.executor.cancel_workflow("wf_does_not_exist", "reason")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
