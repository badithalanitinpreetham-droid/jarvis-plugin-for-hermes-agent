"""
Tests for the workflow state machine — the piece most likely to break
silently under a scheduler nobody's watching in real time. Each test uses
a fresh temp-dir SQLite file, so no network, no real MemoryCore Gateway,
no Ollama. Written against stdlib unittest so it runs with just `python3
-m unittest` in an offline sandbox; pytest (if installed) collects these
unittest.TestCase classes the same way.

Run: python3 -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402 — stubs httpx in offline sandboxes; no-op if httpx is really installed

from jarvis_memory.tools.autonomous import AutonomousExecutor, validate_plan
from jarvis_memory.workflow_store import WorkflowStore


def make_plan(steps):
    return {"goal": "test goal", "steps": steps}


def high_conf_step(step_id, action="do thing"):
    return {"id": step_id, "action": action, "confidence": 0.95, "risk": "low"}


def low_conf_step(step_id, action="risky thing"):
    return {"id": step_id, "action": action, "confidence": 0.4, "risk": "high"}


class StoreBackedTestCase(unittest.TestCase):
    """Base class: gives each test its own temp SQLite file + a ready executor."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "workflows.db")
        self.store = WorkflowStore(self.db_path)
        self.executor = AutonomousExecutor(
            memory_engine=None, config={"auto_approve_confidence": 0.85}, store=self.store
        )

    def tearDown(self):
        self.store.close()
        self._tmpdir.cleanup()

    def new_executor(self):
        """A second executor instance backed by the SAME store — simulates
        a process restart against the same on-disk state."""
        return AutonomousExecutor(memory_engine=None, config={"auto_approve_confidence": 0.85}, store=self.store)


class TestPlanValidation(unittest.TestCase):
    def test_rejects_missing_steps(self):
        self.assertIsNotNone(validate_plan({"goal": "x"}))

    def test_rejects_empty_steps(self):
        self.assertIsNotNone(validate_plan({"goal": "x", "steps": []}))

    def test_rejects_step_missing_field(self):
        bad = {"goal": "x", "steps": [{"id": 1, "action": "a"}]}  # no confidence/risk
        self.assertIsNotNone(validate_plan(bad))

    def test_accepts_valid_plan(self):
        self.assertIsNone(validate_plan(make_plan([high_conf_step(1)])))


class TestBasicFlow(StoreBackedTestCase):
    def test_high_confidence_step_is_ready_immediately(self):
        plan = make_plan([high_conf_step(1)])
        result = self.executor.start_workflow("wf1", plan, "profile-a")
        self.assertEqual(result["status"], "ready_to_execute")
        self.assertEqual(result["step"]["id"], 1)

    def test_low_confidence_step_pauses_for_approval(self):
        plan = make_plan([low_conf_step(1)])
        result = self.executor.start_workflow("wf2", plan, "profile-a")
        self.assertEqual(result["status"], "awaiting_approval")

    def test_full_two_step_run_completes(self):
        plan = make_plan([high_conf_step(1), high_conf_step(2)])
        result = self.executor.start_workflow("wf3", plan, "profile-a")
        self.assertEqual(result["status"], "ready_to_execute")

        result = self.executor.report_step_result("wf3", 1, "success", output="ok")
        self.assertEqual(result["status"], "ready_to_execute")
        self.assertEqual(result["step"]["id"], 2)

        result = self.executor.report_step_result("wf3", 2, "success", output="ok")
        self.assertEqual(result["status"], "completed")

    def test_failed_step_marks_completed_with_failures(self):
        plan = make_plan([high_conf_step(1)])
        self.executor.start_workflow("wf4", plan, "profile-a")
        result = self.executor.report_step_result("wf4", 1, "failed", error="boom")
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertEqual(result["failed"], 1)


class TestApprovalGate(StoreBackedTestCase):
    def test_approve_step_hands_back_ready_to_execute(self):
        plan = make_plan([low_conf_step(1)])
        self.executor.start_workflow("wf5", plan, "profile-a")
        result = self.executor.approve_step("wf5", 1)
        self.assertEqual(result["status"], "ready_to_execute")

    def test_approving_wrong_step_id_errors(self):
        plan = make_plan([low_conf_step(1)])
        self.executor.start_workflow("wf6", plan, "profile-a")
        result = self.executor.approve_step("wf6", 999)
        self.assertIn("error", result)

    def test_gate_stays_up_until_explicitly_approved(self):
        plan = make_plan([low_conf_step(1)])
        self.executor.start_workflow("wf7", plan, "profile-a")
        status = self.executor.get_workflow_status("wf7")
        self.assertEqual(status["status"], "awaiting_approval")


class TestIdempotency(StoreBackedTestCase):
    def test_double_start_resumes_instead_of_resetting(self):
        plan = make_plan([high_conf_step(1), high_conf_step(2)])
        self.executor.start_workflow("wf8", plan, "profile-a")
        self.executor.report_step_result("wf8", 1, "success")

        # Simulate a retried cron tick calling start_workflow again with the
        # same workflow_id — must NOT reset next_index back to 0.
        result = self.executor.start_workflow("wf8", plan, "profile-a")
        self.assertEqual(result["step"]["id"], 2, "start_workflow must resume, not reset, an in-flight run")

    def test_replayed_report_does_not_double_count(self):
        plan = make_plan([high_conf_step(1)])
        self.executor.start_workflow("wf9", plan, "profile-a")
        self.executor.report_step_result("wf9", 1, "success")
        # A retried report for the same already-processed step must fail
        # cleanly rather than double-appending to completed_steps.
        result = self.executor.report_step_result("wf9", 1, "success")
        self.assertIn("error", result)
        status = self.executor.get_workflow_status("wf9")
        self.assertEqual(status["completed_steps"], [1])

    def test_dedupe_key_skips_on_replay_across_workflows(self):
        step = high_conf_step(1)
        step["dedupe_key"] = "publish:episode-42"
        plan = make_plan([step])

        self.executor.start_workflow("wf10", plan, "profile-a")
        self.executor.report_step_result("wf10", 1, "success")

        # A second, distinct workflow (e.g. a crash-triggered replan) that
        # includes a step with the same dedupe_key must never be handed
        # back to Hermes as ready_to_execute — it already went out once.
        step2 = high_conf_step(1)
        step2["dedupe_key"] = "publish:episode-42"
        plan2 = make_plan([step2, high_conf_step(2)])
        result = self.executor.start_workflow("wf11", plan2, "profile-a")
        self.assertEqual(result["status"], "ready_to_execute")
        self.assertEqual(
            result["step"]["id"], 2,
            "step 1 should have been auto-skipped as an already-completed duplicate"
        )


class TestPersistence(StoreBackedTestCase):
    def test_workflow_survives_executor_restart(self):
        plan = make_plan([high_conf_step(1), high_conf_step(2)])
        self.executor.start_workflow("wf12", plan, "profile-a")
        self.executor.report_step_result("wf12", 1, "success")

        # Simulate a process restart: a brand-new executor instance backed
        # by the same on-disk store must pick up exactly where it left off.
        exec2 = self.new_executor()
        result = exec2.get_next_step("wf12")
        self.assertEqual(result["step"]["id"], 2)

    def test_list_pending_approvals_across_restart(self):
        plan = make_plan([low_conf_step(1)])
        self.executor.start_workflow("wf13", plan, "profile-a")

        exec2 = self.new_executor()
        pending = exec2.list_pending_approvals()
        self.assertTrue(any(w["profile_id"] == "profile-a" for w in pending))


class TestReflection(StoreBackedTestCase):
    def test_reflect_captures_a_lesson_via_memory_engine(self):
        captured = {}

        class FakeMemory:
            def add_memory(self, user_id, text, metadata):
                captured["user_id"] = user_id
                captured["text"] = text
                captured["metadata"] = metadata
                return {"status": "success"}

        self.executor.memory_engine = FakeMemory()
        plan = make_plan([high_conf_step(1)])
        self.executor.start_workflow("wf14", plan, "profile-a")
        self.executor.report_step_result("wf14", 1, "success")

        result = self.executor.reflect("wf14")
        self.assertIn("Success rate: 100%", result["lesson"])
        self.assertEqual(captured["metadata"]["type"], "workflow_reflection")


if __name__ == "__main__":
    unittest.main()
